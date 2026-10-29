import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_dense_chat_api import FakeLLM
from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex, SAMPLE
from docscope.api import create_app


class DocumentLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = FakeIndex()
        self.llm = FakeLLM(response=lambda _, evidence: {
            "status": "answered", "answer": "North Observatory.",
            "citation_ids": [evidence[0]["chunk_id"]],
        })
        self.client = self.make_client()

    def make_client(self):
        client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(), index=self.index, llm=self.llm))
        self.addCleanup(client.close)
        return client

    def upload(self, content=SAMPLE, filename="notes.md", document_id=None):
        method, url = ("POST", "/api/documents") if document_id is None else ("PUT", f"/api/documents/{document_id}")
        return self.client.request(method, url, files={"file": (filename, content, "text/markdown")})

    def search(self):
        return self.client.post("/api/retrieval/dense", json={"query": "Orion", "limit": 10}).json()["evidence"]

    def test_identical_reupload_preserves_document_and_chunks_after_restart(self):
        first = self.upload().json()
        chunks = {item["chunk_id"] for item in self.search()}
        self.client = self.make_client()
        repeated = self.upload()
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["document_id"], first["document_id"])
        self.assertEqual({item["chunk_id"] for item in self.search()}, chunks)
        self.assertEqual(len(self.index.points), 2)
        documents = self.client.get("/api/documents").json()["documents"]
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["status"], "ready")
        self.assertEqual(documents[0]["chunk_count"], 2)

    def test_replace_delete_and_return_to_original_content_invalidate_old_citations(self):
        first = self.upload().json()
        document_id = first["document_id"]
        old = self.client.post("/api/chat", json={"question": "Who built Orion?"}).json()["citations"][0]
        citation_url = f"/api/documents/{document_id}/citations/{old['chunk_id']}"
        checked = self.client.get(citation_url)
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["status"], "valid")

        changed = self.upload(b"# Notes\nOrion is now maintained by the East Observatory.")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["document_id"], document_id)
        self.assertEqual(len(self.index.points), 1)
        self.assertTrue(all("North Observatory" not in item["chunk_text"] for item in self.search()))
        self.assertIn("East Observatory", self.search()[0]["chunk_text"])
        self.assertEqual(self.client.get(citation_url).json()["status"], "invalid")

        restored = self.upload(document_id=document_id)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["document_id"], document_id)
        self.assertNotIn(old["chunk_id"], {item["chunk_id"] for item in self.search()})
        self.assertEqual(self.client.get(citation_url).json()["status"], "invalid")

        deleted = self.client.delete(f"/api/documents/{document_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["status"], "deleted")
        self.assertEqual(self.search(), [])
        self.assertEqual(self.index.points, [])
        self.assertFalse((self.root / first["source_path"]).exists())
        self.assertEqual(self.client.get("/api/documents").json()["documents"], [])
        self.assertEqual(self.client.get(citation_url).json()["status"], "invalid")
        self.assertEqual(self.client.delete(f"/api/documents/{document_id}").status_code, 200)
        self.assertEqual(self.client.delete("/api/documents/unknown").status_code, 404)
        self.assertEqual(self.upload(document_id="unknown").status_code, 404)

    def test_failed_replacement_and_delete_remain_hidden_and_can_be_retried(self):
        document_id = self.upload().json()["document_id"]
        original_upsert = self.index.upsert
        def broken_upsert(points):
            original_upsert(points)
            self.index.fail_cleanup = True
            raise RuntimeError("partial index write")
        self.index.upsert = broken_upsert
        changed = b"# Notes\nOrion is maintained by East Observatory."
        failed = self.upload(changed)
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["document_id"], document_id)
        self.assertEqual(failed.json()["error_code"], "index_failed")
        self.assertTrue(self.index.points)
        self.assertEqual(self.search(), [])
        self.assertEqual(self.client.get("/api/documents").json()["documents"][0]["status"], "failed")
        self.index.upsert = original_upsert
        self.index.fail_cleanup = False
        self.assertEqual(self.upload(changed).status_code, 200)
        self.assertEqual(len(self.index.points), 1)

        self.index.fail_cleanup = True
        failed_delete = self.client.delete(f"/api/documents/{document_id}")
        self.assertEqual(failed_delete.status_code, 503)
        self.assertEqual(failed_delete.json()["error_code"], "delete_failed")
        self.client = self.make_client()
        self.assertEqual(self.search(), [])
        self.index.fail_cleanup = False
        self.assertEqual(self.client.delete(f"/api/documents/{document_id}").status_code, 200)
        self.assertEqual(self.index.points, [])

    def test_document_changed_during_generation_does_not_return_stale_answer(self):
        self.upload()
        def replacing_model(_, evidence):
            self.assertEqual(self.upload(b"# Notes\nOrion now belongs to East Observatory.").status_code, 200)
            return {"status": "answered", "answer": "North Observatory", "citation_ids": [evidence[0]["chunk_id"]]}
        self.llm.response = replacing_model
        answer = self.client.post("/api/chat", json={"question": "Who built Orion?"})
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(answer.json()["error_code"], "source_changed")
        self.assertEqual(answer.json()["citations"], [])

    def test_interrupted_ingestion_becomes_failed_on_restart_and_can_retry(self):
        document_id = self.upload().json()["document_id"]
        # Persisted crash fixture: the process stopped after claiming a mutation.
        with sqlite3.connect(self.root / "documents.sqlite3") as database:
            database.execute("UPDATE documents SET status = 'processing' WHERE document_id = ?", (document_id,))
        self.client = self.make_client()
        record = self.client.get(f"/api/documents/{document_id}").json()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error_code"], "interrupted")
        self.assertEqual(self.search(), [])
        self.assertEqual(self.upload().json()["document_id"], document_id)
        self.assertEqual(len(self.index.points), 2)

    def test_changed_uncited_evidence_blocks_answers_and_refusals(self):
        cited_id = self.upload().json()["document_id"]
        for outcome in ("answered", "insufficient_evidence"):
            with self.subTest(outcome=outcome):
                other_id = self.upload(b"# Notes\nOrion has another source.", "other.md").json()["document_id"]
                def replacing_model(_, evidence):
                    self.assertIn(other_id, {item["document_id"] for item in evidence})
                    self.assertEqual(self.upload(b"# Notes\nOrion has changed evidence.", "other.md", other_id).status_code, 200)
                    cited = next(item for item in evidence if item["document_id"] == cited_id)
                    return {
                        "status": outcome,
                        "answer": "North Observatory." if outcome == "answered" else None,
                        "citation_ids": [cited["chunk_id"]] if outcome == "answered" else [],
                    }
                self.llm.response = replacing_model
                response = self.client.post("/api/chat", json={"question": "Who built Orion?"})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error_code"], "source_changed")
                self.assertIsNone(response.json()["answer"])
                self.assertEqual(response.json()["citations"], [])

    def test_concurrent_duplicate_uploads_and_processing_state(self):
        original_upsert = self.index.upsert
        observed = []
        def observing_upsert(points):
            observed.extend(self.client.get("/api/documents").json()["documents"])
            original_upsert(points)
        self.index.upsert = observing_upsert
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.upload(), range(2)))
        self.assertEqual(sorted(result.status_code for result in results), [200, 201])
        self.assertEqual(len({result.json()["document_id"] for result in results}), 1)
        self.assertEqual(len(self.index.points), 2)
        self.assertEqual(observed[0]["status"], "processing")

    def test_legacy_store_and_ambiguous_filenames_are_preserved(self):
        first = self.upload().json()
        old_evidence = self.search()[0]
        # Recreate the six-column DOCS-006 schema, including a historical duplicate.
        with sqlite3.connect(self.root / "documents.sqlite3") as database:
            database.execute("ALTER TABLE documents RENAME TO newer_documents")
            database.execute("""CREATE TABLE documents (document_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL, source_path TEXT NOT NULL, content_hash TEXT NOT NULL,
                status TEXT NOT NULL, error_code TEXT)""")
            database.execute("INSERT INTO documents SELECT document_id, filename, source_path, content_hash, status, error_code FROM newer_documents")
            database.execute("DROP TABLE newer_documents")
        self.client = self.make_client()
        repeated = self.upload()
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["document_id"], first["document_id"])
        checked = self.client.get(f"/api/documents/{first['document_id']}/citations/{old_evidence['chunk_id']}")
        self.assertEqual(checked.json()["status"], "valid")
        with sqlite3.connect(self.root / "documents.sqlite3") as database:
            database.execute("""INSERT INTO documents (document_id, filename, source_path, content_hash, status)
                VALUES ('legacy-duplicate', 'notes.md', 'documents/unused.md', 'unused', 'failed')""")
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(len(self.client.get("/api/documents").json()["documents"]), 2)
        self.assertEqual(self.upload(document_id=first["document_id"]).status_code, 200)

    def test_format_change_removes_old_source_and_name_conflict_preserves_document(self):
        first = self.upload().json()
        pdf = (Path(__file__).parent / "fixtures" / "observatories.pdf").read_bytes()
        replacement = self.upload(pdf, "observatories.pdf", first["document_id"])
        self.assertEqual(replacement.status_code, 200)
        self.assertFalse((self.root / first["source_path"]).exists())
        self.assertTrue((self.root / replacement.json()["source_path"]).exists())
        self.assertEqual(self.search()[0]["page_number"], 2)
        other = self.upload(filename="other.md").json()
        conflict = self.upload(filename="other.md", document_id=first["document_id"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.get(f"/api/documents/{first['document_id']}").json()["filename"], "observatories.pdf")
        self.assertEqual(self.client.get(f"/api/documents/{other['document_id']}").json()["status"], "ready")

    def test_citation_lookup_failure_is_not_reported_as_invalid(self):
        document_id = self.upload().json()["document_id"]
        chunk_id = self.search()[0]["chunk_id"]
        def broken_lookup(_):
            raise RuntimeError("Qdrant unavailable")
        self.index.get_point = broken_lookup
        response = self.client.get(f"/api/documents/{document_id}/citations/{chunk_id}")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
