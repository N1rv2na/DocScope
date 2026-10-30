"""DOCS-009 hybrid retrieval contract without model or external services."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex, SAMPLE
from docscope.api import create_app
from docscope.evaluation.run_hybrid import run, summarize
from docscope.qdrant_index import QdrantError, QdrantIndex


class QdrantHybridContractTest(unittest.TestCase):
    def test_existing_dense_collection_gets_bm25_index_once(self):
        index = QdrantIndex("http://localhost:6333", "chunks")
        calls = []
        schemas = iter([{}, {"chunk_text": {"data_type": "text", "params": {
            "tokenizer": "word", "lowercase": True, "scoring": {"type": "bm25"}}}}])

        def request(method, path, body=None):
            calls.append((method, path, body))
            if path == "/":
                return {"version": "1.20.0"}
            if path == "/collections/chunks" and method == "GET":
                return {"result": {"payload_schema": next(schemas)}}
            return {"result": True}

        index._request = request
        index.ensure()
        index.ensure()
        indexes = [call for call in calls if "/index?" in call[1]]
        self.assertEqual(len(indexes), 1)
        self.assertEqual(indexes[0][2]["field_schema"]["scoring"]["type"], "bm25")
        self.assertEqual(indexes[0][2]["field_schema"]["tokenizer"], "word")

    def test_incompatible_existing_text_index_fails_closed(self):
        index = QdrantIndex("http://localhost:6333", "chunks")
        index._request = lambda method, path, body=None: (
            {"version": "1.20.0"} if path == "/" else
            {"result": {"payload_schema": {"chunk_text": {"data_type": "text", "params": {"tokenizer": "prefix"}}}}}
        )
        with self.assertRaises(QdrantError):
            index.ensure()

    def test_hybrid_uses_same_filter_and_bounded_prefetches(self):
        index = QdrantIndex("http://localhost:6333", "chunks")
        bodies = []
        index._request = lambda method, path, body: bodies.append(body) or {"result": {"points": []}}
        index.query_hybrid([1.0], "Orion", ["ready"], 5, 20)
        body = bodies[0]
        self.assertEqual(body["query"], {"fusion": "rrf"})
        self.assertEqual(body["limit"], 5)
        self.assertEqual([item["limit"] for item in body["prefetch"]], [20, 20])
        self.assertEqual(body["prefetch"][1]["query"], {"text": "Orion"})
        self.assertEqual(body["prefetch"][0]["filter"], body["prefetch"][1]["filter"])
        self.assertEqual(body["prefetch"][0]["filter"]["must"][0]["match"]["any"], ["ready"])
        index.query_hybrid([1.0], "Orion", None, 5, 20)
        self.assertTrue(all("filter" not in part for part in bodies[1]["prefetch"]))


class HybridApiTest(unittest.TestCase):
    def test_dense_and_hybrid_share_chunk_identity_and_lifecycle(self):
        class HybridFakeIndex(FakeIndex):
            def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
                self.last_limits = (limit, candidate_limit)
                return self.query(vector, ready_ids, limit)

        with tempfile.TemporaryDirectory() as directory:
            index = HybridFakeIndex()
            with TestClient(create_app(Path(directory), FakeEmbedder(), index)) as client:
                first = client.post("/api/documents", files={"file": ("notes.md", SAMPLE)})
                self.assertEqual(first.status_code, 201)
                doc_id = first.json()["document_id"]
                dense = client.post("/api/retrieval/dense", json={"query": "Orion", "limit": 2}).json()["evidence"]
                hybrid = client.post("/api/retrieval/hybrid", json={"query": "Orion", "limit": 2, "candidate_limit": 3}).json()["evidence"]
                self.assertEqual({row["chunk_id"] for row in dense}, {row["chunk_id"] for row in hybrid})
                self.assertEqual(hybrid[0]["retrieval_stage"], "hybrid")
                self.assertEqual(index.last_limits, (2, 3))
                self.assertEqual(client.post("/api/retrieval/hybrid", json={"query": "Orion", "candidate_limit": 0}).status_code, 422)
                self.assertEqual(client.post("/api/retrieval/hybrid", json={"query": "Orion", "candidate_limit": 51}).status_code, 422)
                self.assertEqual(client.post("/api/retrieval/hybrid", json={"query": "Orion", "limit": 11}).status_code, 422)
                changed = client.put(f"/api/documents/{doc_id}", files={"file": ("notes.md", b"# Orion\nOrion now belongs to East Observatory.")})
                self.assertEqual(changed.status_code, 200)
                current = client.post("/api/retrieval/hybrid", json={"query": "Orion"}).json()["evidence"]
                self.assertEqual(len(current), 1)
                self.assertNotIn(current[0]["chunk_id"], {row["chunk_id"] for row in hybrid})
                def failed_query(*_):
                    raise QdrantError("unavailable")
                index.query_hybrid = failed_query
                self.assertEqual(client.post("/api/retrieval/hybrid", json={"query": "Orion"}).status_code, 503)
                self.assertEqual(client.delete(f"/api/documents/{doc_id}").status_code, 200)
                self.assertEqual(client.post("/api/retrieval/hybrid", json={"query": "Orion"}).json()["evidence"], [])


class HybridEvaluationTest(unittest.TestCase):
    def test_paired_metrics_keep_article_and_fact_denominators_separate(self):
        rows = [
            {"mapping_status": "mapped", "dense": {"retrieval_seconds": 0.1, "metrics": {
                "5": {"article_recall": 1.0, "all_articles": True, "fact_coverage": 0.0, "all_facts": False}}},
             "hybrid": {"retrieval_seconds": 0.2, "metrics": {
                "5": {"article_recall": 1.0, "all_articles": True, "fact_coverage": 1.0, "all_facts": True}}}},
            {"mapping_status": "unmapped", "dense": {"retrieval_seconds": 0.3, "metrics": {
                "5": {"article_recall": 0.0, "all_articles": False, "fact_coverage": None, "all_facts": None}}},
             "hybrid": {"retrieval_seconds": 0.4, "metrics": {
                "5": {"article_recall": 0.0, "all_articles": False, "fact_coverage": None, "all_facts": None}}}},
        ]
        summary = summarize(rows, [5])
        self.assertEqual(summary["dense"]["article_recall@5"], {"mean": 0.5, "denominator": 2})
        self.assertEqual(summary["dense"]["fact_coverage@5"], {"mean": 0.0, "denominator": 1})
        self.assertEqual(summary["hybrid"]["fact_coverage@5"], {"mean": 1.0, "denominator": 1})

    def test_sealed_test_split_is_rejected_before_any_service_call(self):
        with self.assertRaisesRegex(ValueError, "dev only"):
            run(Path("."), {"split": "test"}, Path("unused.json"))


if __name__ == "__main__":
    unittest.main()
