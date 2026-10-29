import unittest

from docscope.evaluation.dense_baseline import map_facts, score_retrieval, fixed_subset
from docscope.evaluation.run_dense import offline_dense_query, EvaluationIndex
from docscope.qdrant_index import QdrantIndex, QdrantError
from unittest.mock import patch


class DenseBaselineTest(unittest.TestCase):
    def test_article_hit_does_not_count_as_fact_hit(self):
        chunks = [{"chunk_id": "a", "chunk_text": "The Orion telescope was built by North Observatory."},
                  {"chunk_id": "b", "chunk_text": "The Vega telescope was built by South Observatory."}]
        mapped = map_facts([{"document_id": "doc", "fact": "Orion telescope was built by North Observatory."}], {"doc": chunks})
        self.assertEqual(mapped["status"], "mapped")
        hit = score_retrieval([{"document_id": "doc", "chunk_id": "b"}], mapped, ["doc"], 1)
        self.assertEqual(hit["article_recall"], 1.0)
        self.assertEqual(hit["fact_coverage"], 0.0)

    def test_unmapped_and_null_have_distinct_denominators(self):
        unmapped = map_facts([{"document_id": "doc", "fact": "Absent sentence"}], {"doc": [{"chunk_id": "a", "chunk_text": "Other text"}]})
        self.assertEqual(unmapped["status"], "unmapped")
        self.assertEqual(score_retrieval([], unmapped, ["doc"], 5)["fact_coverage"], None)
        null = map_facts([], {})
        self.assertEqual(null["status"], "null_query")
        self.assertEqual(score_retrieval([], null, [], 5)["article_recall"], None)

    def test_ambiguous_fact_is_not_silently_scored(self):
        mapping = map_facts([{"document_id": "doc", "fact": "North Observatory"}],
                            {"doc": [{"chunk_id": "a", "chunk_text": "North Observatory."},
                                     {"chunk_id": "b", "chunk_text": "North Observatory."}]})
        self.assertEqual(mapping["failures"][0]["reason"], "ambiguous_chunks")
        self.assertIsNone(score_retrieval([{"document_id": "doc", "chunk_id": "a"}], mapping, ["doc"], 5)["fact_coverage"])

    def test_subset_is_stable_and_stratified(self):
        rows = [{"sample_id": f"{kind}-{i}", "question_type": kind} for kind in ("inference_query", "comparison_query", "temporal_query", "null_query") for i in range(4)]
        self.assertEqual(fixed_subset(rows, 2), fixed_subset(list(reversed(rows)), 2))
        self.assertEqual(len(fixed_subset(rows, 2)), 8)

    def test_long_frozen_question_uses_same_dense_index_without_api_limit(self):
        class Embedder:
            def encode(self, texts):
                self.seen = texts
                return [[1.0]]
        class Index:
            def query(self, vector, ready_ids, limit):
                self.args = (vector, ready_ids, limit)
                return [{"payload": {"document_id": "doc", "chunk_id": "chunk"}, "score": 0.8}]
        embedder, index = Embedder(), Index()
        question = "Which telescope? " * 40
        self.assertGreater(len(question), 500)
        result = offline_dense_query(embedder, index, question, ["doc"], 10)
        self.assertEqual(embedder.seen, [question])
        self.assertEqual(index.args, ([1.0], ["doc"], 10))
        self.assertEqual(result[0]["chunk_id"], "chunk")

    def test_isolated_collection_query_omits_online_ready_id_filter(self):
        index = QdrantIndex("http://localhost:6333", "isolated")
        captured = []
        index._request = lambda method, path, body: captured.append(body) or {"result": {"points": []}}
        index.query([1.0], None, 10)
        self.assertNotIn("filter", captured[0])

    def test_offline_index_retries_connection_failure_but_not_http_failure(self):
        index = EvaluationIndex("http://localhost:6333", "isolated")
        with patch.object(QdrantIndex, "_request", side_effect=[QdrantError("timed out"), {"result": "ok"}]) as request:
            self.assertEqual(index._request("GET", "/"), {"result": "ok"})
            self.assertEqual(request.call_count, 2)
        with patch.object(QdrantIndex, "_request", side_effect=QdrantError("bad", 400)) as request:
            with self.assertRaises(QdrantError):
                index._request("GET", "/")
            self.assertEqual(request.call_count, 1)
