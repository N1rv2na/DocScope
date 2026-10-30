"""Opt-in real Qdrant check; deterministic vectors and answers, no model calls."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_dense_chat_api import FakeLLM
from docscope.api import create_app
from docscope.qdrant_index import QdrantIndex, QdrantError


class FixedEmbedder:
    def encode(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]


@unittest.skipUnless(os.getenv("QDRANT_TEST_URL"), "set QDRANT_TEST_URL for the real Qdrant lifecycle check")
class QdrantLifecycleTest(unittest.TestCase):
    def test_existing_dense_collection_backfills_bm25_on_same_points(self):
        index = QdrantIndex(os.environ["QDRANT_TEST_URL"], collection=f"docscope_test_{uuid.uuid4().hex}")
        self.addCleanup(lambda: index._request("DELETE", f"/collections/{index.collection}"))
        index._request("PUT", f"/collections/{index.collection}", {
            "vectors": {"dense": {"size": 384, "distance": "Cosine"}},
        })
        chunk_id = str(uuid.uuid4())
        index.upsert([{"id": chunk_id, "vector": {"dense": [1.0] + [0.0] * 383},
                       "payload": {"chunk_id": chunk_id, "chunk_text": "Zephyr belongs to West Observatory."}}])
        index.ensure()
        index.ensure()
        lexical = index._request("POST", f"/collections/{index.collection}/points/query", {
            "query": {"text": "Zephyr"}, "using": "chunk_text", "limit": 5, "with_payload": True,
        })["result"]["points"]
        self.assertEqual([point["payload"]["chunk_id"] for point in lexical], [chunk_id])

    def test_bm25_and_rrf_share_dense_point_ids(self):
        index = QdrantIndex(os.environ["QDRANT_TEST_URL"], collection=f"docscope_test_{uuid.uuid4().hex}")
        self.addCleanup(lambda: index._request("DELETE", f"/collections/{index.collection}"))
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(Path(root), FixedEmbedder(), index)) as client:
            first = client.post("/api/documents", files={"file": ("orion.md", b"# Orion\nOrion belongs to North Observatory.")})
            second = client.post("/api/documents", files={"file": ("vega.md", b"# Vega\nVega belongs to South Observatory.")})
            self.assertEqual((first.status_code, second.status_code), (201, 201))
            lexical = index._request("POST", f"/collections/{index.collection}/points/query", {
                "query": {"text": "Vega"}, "using": "chunk_text", "limit": 2, "with_payload": True,
            })["result"]["points"]
            self.assertEqual([point["payload"]["document_id"] for point in lexical], [second.json()["document_id"]])
            hybrid = client.post("/api/retrieval/hybrid", json={"query": "Vega", "limit": 2,
                                                                  "candidate_limit": 2})
            self.assertEqual(hybrid.status_code, 200)
            self.assertIn(lexical[0]["payload"]["chunk_id"],
                          {point["chunk_id"] for point in hybrid.json()["evidence"]})

    def test_reupload_replace_citation_lookup_and_delete(self):
        index = QdrantIndex(os.environ["QDRANT_TEST_URL"], collection=f"docscope_test_{uuid.uuid4().hex}")
        def lexical_ids(text):
            result = index._request("POST", f"/collections/{index.collection}/points/query", {
                "query": {"text": text}, "using": "chunk_text", "limit": 5,
                "with_payload": True,
            })["result"]["points"]
            return [point["payload"]["chunk_id"] for point in result]
        def cleanup():
            try:
                index._request("DELETE", f"/collections/{index.collection}")
            except QdrantError as error:
                if error.status_code != 404:
                    raise
        self.addCleanup(cleanup)
        llm = FakeLLM(response=lambda _, evidence: {
            "status": "answered", "answer": "A fixture answer.", "citation_ids": [evidence[0]["chunk_id"]],
        })
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(Path(root), FixedEmbedder(), index, llm)) as client:
            first_text = b"# Orion\nThe Orion telescope belongs to North Observatory."
            first = client.post("/api/documents", files={"file": ("notes.md", first_text)})
            self.assertEqual(first.status_code, 201)
            document_id = first.json()["document_id"]
            answer = client.post("/api/chat", json={"question": "Who owns Orion?"})
            self.assertEqual(answer.status_code, 200)
            old = answer.json()["citations"][0]
            citation_url = f"/api/documents/{document_id}/citations/{old['chunk_id']}"
            self.assertEqual(client.get(citation_url).json()["status"], "valid")
            self.assertEqual(client.post("/api/documents", files={"file": ("notes.md", first_text)}).status_code, 200)
            self.assertEqual(len(client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"]), 1)
            hybrid = client.post("/api/retrieval/hybrid", json={"query": "Orion North", "limit": 2,
                                                                  "candidate_limit": 3})
            self.assertEqual(hybrid.status_code, 200)
            self.assertEqual([item["chunk_id"] for item in hybrid.json()["evidence"]], [old["chunk_id"]])
            self.assertEqual(lexical_ids("North"), [old["chunk_id"]])
            changed = client.put(f"/api/documents/{document_id}", files={"file": ("notes.md", b"# Orion\nOrion now belongs to East Observatory.")})
            self.assertEqual(changed.status_code, 200)
            evidence = client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"]
            self.assertEqual(len(evidence), 1)
            self.assertIn("East Observatory", evidence[0]["chunk_text"])
            current_hybrid = client.post("/api/retrieval/hybrid", json={"query": "Orion East"}).json()["evidence"]
            self.assertEqual([item["chunk_id"] for item in current_hybrid], [evidence[0]["chunk_id"]])
            self.assertNotIn(old["chunk_id"], {item["chunk_id"] for item in current_hybrid})
            self.assertEqual(lexical_ids("North"), [])
            self.assertEqual(lexical_ids("East"), [evidence[0]["chunk_id"]])
            self.assertEqual(client.get(citation_url).json()["status"], "invalid")
            self.assertEqual(client.delete(f"/api/documents/{document_id}").status_code, 200)
            self.assertEqual(client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"], [])
            self.assertEqual(client.post("/api/retrieval/hybrid", json={"query": "Orion"}).json()["evidence"], [])
            self.assertEqual(lexical_ids("East"), [])
            self.assertIsNone(index.get_point(evidence[0]["chunk_id"]))
