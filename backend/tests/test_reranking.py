"""DOCS-010 bounded Cross-Encoder ranking and three-mode evaluation contract."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex, SAMPLE
from docscope.api import create_app
from docscope.evaluation.run_hybrid import summarize
from docscope.reranking import rerank_points


class FixedReranker:
    def score(self, question, texts):
        self.last = (question, texts)
        return [float(i) for i in range(len(texts))]


class RerankPointsTest(unittest.TestCase):
    def setUp(self):
        self.points = [
            {"id": "a", "score": 0.9, "payload": {"chunk_id": "a", "chunk_text": "First evidence"}},
            {"id": "b", "score": 0.4, "payload": {"chunk_id": "b", "chunk_text": "Second evidence"}},
        ]

    def test_reorders_same_points_and_preserves_prior_rank_and_score(self):
        scorer = FixedReranker()
        result = rerank_points("Which one?", self.points, scorer, 1)
        self.assertEqual([p["id"] for p in result], ["b"])
        self.assertEqual(result[0]["rrf_rank"], 2)
        self.assertEqual(result[0]["rrf_score"], 0.4)
        self.assertEqual(result[0]["rerank_score"], 1.0)
        self.assertEqual(scorer.last, ("Which one?", ["First evidence", "Second evidence"]))
        self.assertEqual(self.points[1]["score"], 0.4)
        self.assertNotIn("rerank_score", self.points[1])

    def test_empty_and_invalid_scores_do_not_produce_false_evidence(self):
        scorer = FixedReranker()
        self.assertEqual(rerank_points("q", [], scorer, 5), [])
        self.assertFalse(hasattr(scorer, "last"))
        scorer.score = lambda q, texts: [float("nan"), 0.0]
        with self.assertRaisesRegex(ValueError, "finite"):
            rerank_points("q", self.points, scorer, 2)
        scorer.score = lambda q, texts: [1.0]
        with self.assertRaisesRegex(ValueError, "count"):
            rerank_points("q", self.points, scorer, 2)
        with self.assertRaisesRegex(ValueError, "candidate"):
            rerank_points("q", self.points * 11, scorer, 2)


class RerankApiTest(unittest.TestCase):
    def test_switchable_rerank_preserves_identity_and_bounds(self):
        class HybridIndex(FakeIndex):
            def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
                self.last_limits = (limit, candidate_limit)
                return self.query(vector, ready_ids, limit)

        with tempfile.TemporaryDirectory() as directory:
            index, scorer = HybridIndex(), FixedReranker()
            with TestClient(create_app(Path(directory), FakeEmbedder(), index, reranker=scorer)) as client:
                uploaded = client.post("/api/documents", files={"file": ("notes.md", SAMPLE)})
                self.assertEqual(uploaded.status_code, 201)
                hybrid = client.post("/api/retrieval/hybrid", json={"query": "Orion", "limit": 2}).json()["evidence"]
                result = client.post("/api/retrieval/rerank", json={
                    "query": "Orion", "limit": 1, "candidate_limit": 3, "rerank_limit": 2})
                self.assertEqual(result.status_code, 200)
                evidence = result.json()["evidence"]
                self.assertEqual(len(evidence), 1)
                self.assertEqual(index.last_limits, (2, 3))
                self.assertIn(evidence[0]["chunk_id"], {item["chunk_id"] for item in hybrid})
                self.assertEqual(evidence[0]["retrieval_stage"], "rerank")
                self.assertEqual(evidence[0]["score"], evidence[0]["rerank_score"])
                self.assertEqual(evidence[0]["rrf_rank"], 2)
                self.assertEqual(evidence[0]["document_id"], uploaded.json()["document_id"])
                self.assertEqual(client.post("/api/retrieval/rerank", json={"query": "Orion", "rerank_limit": 21}).status_code, 422)
                self.assertEqual(client.post("/api/retrieval/rerank", json={"query": "Orion", "limit": 3, "rerank_limit": 2}).status_code, 422)
                self.assertEqual(client.post("/api/retrieval/rerank", json={"query": "Orion", "candidate_limit": 1,
                                                                            "rerank_limit": 2}).status_code, 422)

    def test_stale_candidate_keeps_original_rrf_rank_after_filtering(self):
        class HybridIndex(FakeIndex):
            def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
                current = self.query(vector, ready_ids, limit)[0]
                stale = {**current, "payload": {**current["payload"], "index_version": -1}}
                return [stale, current]

        with tempfile.TemporaryDirectory() as directory:
            index, scorer = HybridIndex(), FixedReranker()
            with TestClient(create_app(Path(directory), FakeEmbedder(), index, reranker=scorer)) as client:
                self.assertEqual(client.post("/api/documents", files={"file": ("notes.md", SAMPLE)}).status_code, 201)
                response = client.post("/api/retrieval/rerank", json={"query": "Orion", "limit": 1,
                                                                     "rerank_limit": 2})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["evidence"][0]["rrf_rank"], 2)
                self.assertEqual(len(scorer.last[1]), 1)

    def test_rerank_evidence_stays_within_chunk_text_and_result_limits(self):
        class HybridIndex(FakeIndex):
            def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
                return self.query(vector, ready_ids, limit)

        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(Path(directory), FakeEmbedder(), HybridIndex(),
                                       reranker=FixedReranker())) as client:
                body = b"# Long\n" + b"Orion observatory evidence. " * 80
                self.assertEqual(client.post("/api/documents", files={"file": ("long.md", body)}).status_code, 201)
                response = client.post("/api/retrieval/rerank", json={"query": "Orion", "limit": 10})
                self.assertEqual(response.status_code, 200)
                evidence = response.json()["evidence"]
                self.assertLessEqual(len(evidence), 10)
                self.assertTrue(all(len(item["chunk_text"]) <= 1000 for item in evidence))

    def test_scoring_failure_is_explicit_and_deleted_document_is_not_scored(self):
        class HybridIndex(FakeIndex):
            def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
                return self.query(vector, ready_ids, limit)

        with tempfile.TemporaryDirectory() as directory:
            index, scorer = HybridIndex(), FixedReranker()
            with TestClient(create_app(Path(directory), FakeEmbedder(), index, reranker=scorer)) as client:
                document_id = client.post("/api/documents", files={"file": ("notes.md", SAMPLE)}).json()["document_id"]
                scorer.score = lambda q, texts: (_ for _ in ()).throw(RuntimeError("model unavailable"))
                self.assertEqual(client.post("/api/retrieval/rerank", json={"query": "Orion"}).status_code, 503)
                self.assertEqual(client.delete(f"/api/documents/{document_id}").status_code, 200)
                self.assertEqual(client.post("/api/retrieval/rerank", json={"query": "Orion"}).json()["evidence"], [])


class ThreeModeSummaryTest(unittest.TestCase):
    def test_rerank_metrics_have_same_denominators(self):
        metrics = {"5": {"article_recall": 1.0, "all_articles": True,
                         "fact_coverage": 0.0, "all_facts": False}}
        row = {"mapping_status": "mapped"}
        for mode in ("dense", "hybrid", "rerank"):
            row[mode] = {"retrieval_seconds": 0.1, "metrics": metrics}
        row["rerank"]["rerank_seconds"] = 0.05
        result = summarize([row], [5])
        self.assertEqual(result["rerank"]["article_recall@5"]["denominator"], 1)
        self.assertEqual(result["rerank"]["fact_coverage@5"]["mean"], 0.0)


if __name__ == "__main__":
    unittest.main()
