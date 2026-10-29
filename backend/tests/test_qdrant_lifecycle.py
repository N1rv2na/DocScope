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
    def test_reupload_replace_citation_lookup_and_delete(self):
        index = QdrantIndex(os.environ["QDRANT_TEST_URL"], collection=f"docscope_test_{uuid.uuid4().hex}")
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
            changed = client.put(f"/api/documents/{document_id}", files={"file": ("notes.md", b"# Orion\nOrion now belongs to East Observatory.")})
            self.assertEqual(changed.status_code, 200)
            evidence = client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"]
            self.assertEqual(len(evidence), 1)
            self.assertIn("East Observatory", evidence[0]["chunk_text"])
            self.assertEqual(client.get(citation_url).json()["status"], "invalid")
            self.assertEqual(client.delete(f"/api/documents/{document_id}").status_code, 200)
            self.assertEqual(client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"], [])
            self.assertIsNone(index.get_point(evidence[0]["chunk_id"]))
