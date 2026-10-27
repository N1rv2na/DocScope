"""PDF imports retain page provenance through Dense Evidence and citations."""

import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.tests.test_dense_chat_api import FakeLLM
from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex, SAMPLE
from docscope.api import create_app


PDF_SAMPLE = (Path(__file__).parent / "fixtures" / "observatories.pdf").read_bytes()


class PdfPageCitationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.index = FakeIndex()
        self.llm = FakeLLM(response=lambda _, evidence: {
            "status": "answered",
            "answer": "The North Observatory built Orion.",
            "citation_ids": [evidence[0]["chunk_id"]],
        })
        self.client = TestClient(create_app(
            data_dir=Path(self.temp.name), embedder=FakeEmbedder(), index=self.index, llm=self.llm,
        ))
        self.addCleanup(self.client.close)

    def upload(self, filename="observatories.pdf", content=PDF_SAMPLE):
        return self.client.post("/api/documents", files={"file": (filename, content, "application/pdf")})

    def test_page_two_fact_keeps_its_page_through_retrieval_and_chat(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 201)
        document = uploaded.json()
        self.assertEqual(document["status"], "ready")
        self.assertEqual(document["chunk_count"], 2)
        self.assertTrue(document["source_path"].endswith(".pdf"))
        self.assertEqual((Path(self.temp.name) / document["source_path"]).read_bytes(), PDF_SAMPLE)

        points = self.index.points
        self.assertEqual([point["payload"]["page_number"] for point in points], [1, 2])
        self.assertTrue(all("Vega" not in point["payload"]["chunk_text"] for point in points[1:]))
        self.assertEqual({point["payload"]["source_type"] for point in points}, {"pdf"})
        evidence = self.client.post("/api/retrieval/dense", json={"query": "Which observatory built Orion?"}).json()["evidence"][0]
        self.assertEqual(evidence["document_id"], document["document_id"])
        self.assertEqual(evidence["page_number"], 2)
        self.assertIsNone(evidence["section"])
        self.assertIn("North Observatory", evidence["chunk_text"])

        answer = self.client.post("/api/chat", json={"question": "Which observatory built Orion?"})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["status"], "answered")
        citation = answer.json()["citations"][0]
        self.assertEqual(citation["chunk_id"], evidence["chunk_id"])
        self.assertEqual(citation["page_number"], 2)
        self.assertIn("North Observatory", citation["chunk_text"])

    def test_broken_and_image_only_pdfs_never_become_ready(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        blank = io.BytesIO()
        writer.write(blank)
        for name, data in (("broken.pdf", b"not a PDF"), ("blank.pdf", blank.getvalue())):
            with self.subTest(name=name):
                uploaded = self.upload(name, data)
                self.assertEqual(uploaded.status_code, 503)
                record = uploaded.json()
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["error_code"], "parse_failed")
                status = self.client.get(f"/api/documents/{record['document_id']}").json()
                self.assertEqual(status["status"], "failed")
        self.assertEqual(self.client.post("/api/retrieval/dense", json={"query": "Orion"}).json()["evidence"], [])

    def test_markdown_and_pdf_share_evidence_and_citation_fields(self):
        self.assertEqual(self.upload().status_code, 201)
        markdown = self.client.post("/api/documents", files={"file": ("telescopes.md", SAMPLE, "text/markdown")})
        self.assertEqual(markdown.status_code, 201)
        evidence = self.client.post("/api/retrieval/dense", json={"query": "Orion", "limit": 5}).json()["evidence"]
        self.assertEqual({item["document_title"] for item in evidence}, {"telescopes.md", "observatories.pdf"})
        self.assertEqual(len({frozenset(item) for item in evidence}), 1)
        markdown_item = next(item for item in evidence if item["document_title"] == "telescopes.md")
        self.assertIsNone(markdown_item["page_number"])
        self.assertEqual(markdown_item["section"], "Telescope Notes > Orion")

    def test_pdf_with_too_many_pages_is_not_indexed(self):
        writer = PdfWriter()
        for _ in range(51):
            writer.append(io.BytesIO(PDF_SAMPLE))
        oversized = io.BytesIO()
        writer.write(oversized)
        self.assertLess(len(oversized.getvalue()), 1024 * 1024)
        uploaded = self.upload("many-pages.pdf", oversized.getvalue())
        self.assertEqual(uploaded.status_code, 503)
        self.assertEqual(uploaded.json()["error_code"], "parse_failed")
        self.assertEqual(self.index.points, [])


if __name__ == "__main__":
    unittest.main()
