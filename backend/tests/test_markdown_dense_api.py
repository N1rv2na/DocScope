import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from docscope.api import create_app


SAMPLE = (Path(__file__).parent / "fixtures" / "telescopes.md").read_bytes()


class FakeEmbedder:
    def encode(self, texts):
        return [[1.0, 0.0] if "Orion" in text else [0.0, 1.0] for text in texts]


class FakeIndex:
    def __init__(self, fail_upsert=False, fail_cleanup=False):
        self.points = []
        self.fail_upsert = fail_upsert
        self.fail_cleanup = fail_cleanup

    def ensure(self):
        pass

    def upsert(self, points):
        self.points.extend(points)
        if self.fail_upsert:
            raise RuntimeError("injected index failure")

    def delete_document(self, document_id):
        if self.fail_cleanup:
            raise RuntimeError("injected cleanup failure")
        self.points = [point for point in self.points if point["payload"]["document_id"] != document_id]

    def get_point(self, chunk_id):
        return next((point for point in self.points if point["id"] == chunk_id), None)

    def query(self, vector, ready_ids, limit):
        scored = []
        for point in self.points:
            if point["payload"]["document_id"] in ready_ids:
                score = sum(a * b for a, b in zip(vector, point["vector"]["dense"]))
                scored.append({"id": point["id"], "score": score, "payload": point["payload"]})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


class MarkdownDenseApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = FakeIndex()
        self.client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(), index=self.index))
        self.addCleanup(self.client.close)

    def upload(self, filename="notes.md", content=SAMPLE):
        return self.client.post("/api/documents", files={"file": (filename, content, "text/markdown")})

    def search(self, query="Where was Orion built?"):
        return self.client.post("/api/retrieval/dense", json={"query": query, "limit": 2})

    def test_upload_then_dense_query_returns_original_chunk_and_section(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 201)
        doc = uploaded.json()
        self.assertEqual(doc["status"], "ready")
        self.assertTrue((self.root / doc["source_path"]).exists())
        self.assertEqual((self.root / doc["source_path"]).read_bytes(), SAMPLE)
        self.assertEqual(self.client.get(f"/api/documents/{doc['document_id']}").json()["status"], "ready")
        result = self.search()
        self.assertEqual(result.status_code, 200)
        evidence = result.json()["evidence"][0]
        self.assertEqual(evidence["document_id"], doc["document_id"])
        self.assertTrue(evidence["chunk_id"])
        self.assertEqual(evidence["section"], "Telescope Notes > Orion")
        self.assertIn("The Orion telescope was built in 2022", evidence["chunk_text"])
        self.assertEqual(evidence["retrieval_stage"], "dense")
        restarted = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(), index=self.index))
        self.addCleanup(restarted.close)
        after_restart = restarted.post("/api/retrieval/dense", json={"query": "Orion"})
        self.assertEqual(after_restart.status_code, 200)
        self.assertEqual(after_restart.json()["evidence"][0]["chunk_id"], evidence["chunk_id"])

    def test_invalid_inputs_do_not_create_usable_documents(self):
        for filename, content, expected in [
            ("empty.md", b"", 400),
            ("blank.md", b" \n ", 400),
            ("notes.pdf", SAMPLE, 503),
            ("../escape.md", SAMPLE, 400),
            (r"C:\temp\escape.md", SAMPLE, 400),
            ("bad.md", b"\xff", 400),
            ("large.md", b"x" * (1024 * 1024 + 1), 413),
        ]:
            with self.subTest(filename=filename):
                self.assertEqual(self.upload(filename, content).status_code, expected)
        self.assertEqual(self.search().json()["evidence"], [])
        self.assertEqual(self.index.points, [])
        self.assertEqual(list((self.root / "documents").glob("*.md")), [])

    def test_failed_index_is_observable_and_unfinished_points_are_invisible(self):
        index = FakeIndex(fail_upsert=True, fail_cleanup=True)
        client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(), index=index))
        self.addCleanup(client.close)
        response = client.post("/api/documents", files={"file": ("notes.md", SAMPLE, "text/markdown")})
        self.assertEqual(response.status_code, 503)
        doc = response.json()
        self.assertEqual(doc["status"], "failed")
        self.assertEqual(doc["error_code"], "index_failed")
        self.assertEqual(client.get(f"/api/documents/{doc['document_id']}").json()["status"], "failed")
        self.assertTrue(index.points)
        self.assertEqual(client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"], [])

    def test_failed_import_is_hidden_when_another_document_is_ready(self):
        ready = self.upload().json()["document_id"]
        self.index.fail_upsert = True
        self.index.fail_cleanup = True
        failed = self.upload("other.md", b"# Other\n## Orion\nFailed import evidence.\n").json()
        self.assertEqual(failed["status"], "failed")
        response = self.search("Orion")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["evidence"])
        self.assertEqual({item["document_id"] for item in response.json()["evidence"]}, {ready})

    def test_markdown_without_body_has_failed_parse_status(self):
        response = self.upload("headings.md", b"# Heading only\n")
        self.assertEqual(response.status_code, 503)
        doc = response.json()
        self.assertEqual(doc["error_code"], "parse_failed")
        self.assertEqual(self.client.get(f"/api/documents/{doc['document_id']}").json()["status"], "failed")
        self.assertEqual(self.search().json()["evidence"], [])

    def test_empty_query_and_missing_document_have_explicit_errors(self):
        self.assertEqual(self.search("  ").status_code, 422)
        self.assertEqual(self.client.get("/api/documents/does-not-exist").status_code, 404)


if __name__ == "__main__":
    unittest.main()
