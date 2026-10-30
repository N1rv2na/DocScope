"""DOCS-011 single-retrieval LangGraph Run at the API boundary."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.test_dense_chat_api import FakeLLM
from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex, SAMPLE
from docscope.api import create_app


class HybridIndex(FakeIndex):
    def __init__(self):
        super().__init__()
        self.hybrid_calls = []

    def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
        self.hybrid_calls.append((text, limit, candidate_limit))
        return self.query(vector, ready_ids, limit)


class AgentRunApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = HybridIndex()
        self.llm = FakeLLM()
        self.client = TestClient(create_app(
            data_dir=self.root, embedder=FakeEmbedder(), index=self.index, llm=self.llm,
        ))
        self.addCleanup(self.client.close)

    def upload(self):
        response = self.client.post("/api/documents", files={"file": ("notes.md", SAMPLE)})
        self.assertEqual(response.status_code, 201)

    def ask(self, question="Which observatory built the Orion telescope?"):
        return self.client.post("/api/agent/run", json={"question": question})

    def saved(self, body):
        path = self.root / "runs" / f"{body['run_id']}.json"
        self.assertTrue(path.is_file())
        return json.loads(path.read_text(encoding="utf-8"))

    def test_one_graph_run_answers_with_current_citation_and_trace(self):
        self.upload()
        self.llm.response = lambda _, evidence: {
            "status": "answered", "answer": "North Observatory.",
            "citation_ids": [evidence[0]["chunk_id"]],
        }
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["answer"], "North Observatory.")
        self.assertEqual(body.get("question"), "Which observatory built the Orion telescope?")
        self.assertEqual(body["query"], "Which observatory built the Orion telescope?")
        self.assertEqual(body["stop_reason"], "answered")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 1})
        self.assertEqual([step["stage"] for step in body["trace"]], ["plan", "retrieve", "answer"])
        self.assertEqual(body["trace"][1]["evidence_ids"], body["retrieved_evidence_ids"])
        self.assertEqual(body["citations"][0]["chunk_id"], body["retrieved_evidence_ids"][0])
        self.assertEqual(self.index.hybrid_calls, [(body["query"], 5, 20)])
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(self.saved(body), body)

    def test_no_evidence_abstains_without_model_call(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["stop_reason"], "no_evidence")
        self.assertIsNone(body["answer"])
        self.assertEqual(body["citations"], [])
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 0})
        self.assertEqual([step["stage"] for step in body["trace"]], ["plan", "retrieve"])
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.saved(body), body)

    def test_model_refusal_retains_evidence_ids_but_no_citations(self):
        self.upload()
        self.llm.response = {"status": "insufficient_evidence", "answer": None, "citation_ids": []}
        body = self.ask("What is Orion's launch mass?").json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["stop_reason"], "model_abstained")
        self.assertTrue(body["retrieved_evidence_ids"])
        self.assertEqual(body["citations"], [])
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 1})
        self.assertEqual(self.saved(body), body)

    def test_retrieval_failure_is_recorded_once_without_success_answer(self):
        self.upload()

        def broken_query(*_):
            raise RuntimeError("index unavailable")

        self.index.query_hybrid = broken_query
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "retrieval_failed")
        self.assertEqual(body["stop_reason"], "retrieval_failed")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 0})
        self.assertEqual(body["trace"][-1]["status"], "failed")
        self.assertIsNone(body["answer"])
        self.assertEqual(body["citations"], [])
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.saved(body), body)

    def test_model_failure_and_fabricated_citation_are_recorded_without_retry(self):
        self.upload()
        for llm_result, expected_code, http_status in [
            (TimeoutError("model unavailable"), "model_failed", 503),
            ({"status": "answered", "answer": "invented", "citation_ids": ["missing"]},
             "model_output_invalid", 502),
        ]:
            with self.subTest(expected_code=expected_code):
                self.llm.calls.clear()
                self.llm.error = llm_result if isinstance(llm_result, Exception) else None
                self.llm.response = llm_result if isinstance(llm_result, dict) else None
                response = self.ask()
                self.assertEqual(response.status_code, http_status)
                body = response.json()
                self.assertEqual(body["status"], "error")
                self.assertEqual(body["error_code"], expected_code)
                self.assertEqual(body["stop_reason"], expected_code)
                self.assertEqual(body["calls"], {"retrieval": 1, "model": 1})
                self.assertEqual(body["trace"][-1]["status"], "failed")
                self.assertIsNone(body["answer"])
                self.assertEqual(body["citations"], [])
                self.assertEqual(len(self.llm.calls), 1)
                self.assertEqual(self.saved(body), body)

    def test_source_replacement_during_model_call_rejects_answer(self):
        self.upload()

        def replace_source(_, evidence):
            response = self.client.put("/api/documents/" + evidence[0]["document_id"],
                                       files={"file": ("notes.md", b"# New\nChanged evidence.")})
            self.assertEqual(response.status_code, 200)
            return {"status": "answered", "answer": "North Observatory.",
                    "citation_ids": [evidence[0]["chunk_id"]]}

        self.llm.response = replace_source
        response = self.ask()
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["error_code"], "source_changed")
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["citations"], [])
        self.assertEqual(self.saved(body), body)

    def test_existing_chat_stays_dense_and_invalid_question_starts_no_run(self):
        self.upload()
        self.llm.response = lambda _, evidence: {
            "status": "answered", "answer": "North Observatory.",
            "citation_ids": [evidence[0]["chunk_id"]],
        }
        old = self.client.post("/api/chat", json={"question": "Orion?"})
        self.assertEqual(old.status_code, 200)
        self.assertEqual(old.json()["status"], "answered")
        self.assertEqual(self.index.hybrid_calls, [])
        agent = self.ask("Orion?")
        self.assertEqual(agent.status_code, 200)
        self.assertEqual(agent.json()["status"], old.json()["status"])
        self.assertEqual(agent.json()["answer"], old.json()["answer"])
        self.assertEqual(agent.json()["citations"], old.json()["citations"])
        existing_records = list((self.root / "runs").glob("*.json"))
        self.assertEqual(self.ask(" ").status_code, 422)
        self.assertEqual(list((self.root / "runs").glob("*.json")), existing_records)

    def test_run_store_failure_returns_failed_trace_and_discards_answer(self):
        self.upload()
        self.llm.response = lambda _, evidence: {
            "status": "answered", "answer": "North Observatory.",
            "citation_ids": [evidence[0]["chunk_id"]],
        }
        with patch("docscope.api.RunStore.save", side_effect=OSError("disk full")):
            response = self.ask()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "run_record_failed")
        self.assertEqual(body.get("stop_reason"), "run_record_failed")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 1})
        self.assertEqual(body["trace"][-1]["stage"], "persist")
        self.assertEqual(body["trace"][-1]["status"], "failed")
        self.assertIsNone(body["answer"])
        self.assertEqual(body["citations"], [])


if __name__ == "__main__":
    unittest.main()
