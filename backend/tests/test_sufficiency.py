"""DOCS-013 stopping, missing-fact, refusal, and citation behavior at the Agent API."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_markdown_dense_api import FakeEmbedder
from backend.tests.test_second_hop import SAMPLE, SAMPLE_DIR, TwoHopIndex
from docscope.api import create_app


class DecisionLLM:
    def __init__(self):
        self.decision = None
        self.answer = None
        self.plan_calls = 0
        self.answer_calls = 0

    def plan_next(self, question, evidence):
        self.plan_calls += 1
        return self.decision(question, evidence)

    def generate(self, question, evidence):
        self.answer_calls += 1
        return self.answer(question, evidence)


class SufficiencyApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.index = TwoHopIndex()
        self.llm = DecisionLLM()
        self.client = TestClient(create_app(data_dir=Path(self.temp.name),
                                            embedder=FakeEmbedder(), index=self.index, llm=self.llm))
        self.addCleanup(self.client.close)
        self.document_ids = []
        for name in (SAMPLE["first_source"], SAMPLE["second_source"]):
            response = self.client.post("/api/documents", files={
                "file": (name, (SAMPLE_DIR / name).read_bytes())})
            self.assertEqual(response.status_code, 201)
            self.document_ids.append(response.json()["document_id"])

    def ask(self, question=None):
        return self.client.post("/api/agent/run", json={"question": question or SAMPLE["question"]})

    def test_sufficient_first_hop_answers_without_second_retrieval(self):
        self.llm.decision = lambda question, evidence: {
            "evidence_status": "sufficient", "missing_fact": None,
            "next_query": None, "bridge_text": None, "source_chunk_id": None}
        self.llm.answer = lambda question, evidence: {
            "status": "answered", "answer": "Polaris Relay.",
            "citation_ids": [evidence[0]["chunk_id"]]}
        response = self.ask("Which service does Aster Telemetry Console use for audit export?")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["stop_reason"], "answered")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 2})
        self.assertEqual(body["trace"][2]["evidence_status"], "sufficient")
        self.assertEqual(body["trace"][2]["reason"], "sufficient")
        self.assertEqual(len(self.index.queries), 1)
        self.assertEqual([item["document_title"] for item in body["citations"]],
                         [SAMPLE["first_source"]])

    def test_missing_fact_after_second_hop_abstains_when_retrieval_budget_exhausted(self):
        self.llm.decision = lambda question, evidence: {
            "evidence_status": "missing", "missing_fact": "the operator of Polaris Relay",
            "next_query": "Polaris Relay operator team", "bridge_text": "Polaris Relay",
            "source_chunk_id": evidence[0]["chunk_id"]}
        self.llm.answer = lambda question, evidence: {
            "status": "insufficient_evidence", "answer": None, "citation_ids": []}
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["stop_reason"], "retrieval_budget_exhausted")
        self.assertEqual(body["answer"], None)
        self.assertEqual(body["citations"], [])
        self.assertEqual(body["calls"], {"retrieval": 2, "model": 2})
        self.assertEqual(body["trace"][2]["missing_fact"], "the operator of Polaris Relay")
        self.assertEqual(len(self.index.queries), 2)

    def test_missing_fact_without_grounded_query_abstains_before_answer(self):
        self.llm.decision = lambda question, evidence: {
            "evidence_status": "missing", "missing_fact": "the operator of Polaris Relay",
            "next_query": None, "bridge_text": None, "source_chunk_id": None}
        self.llm.answer = lambda question, evidence: self.fail("must not generate unsupported answer")
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["stop_reason"], "missing_fact_unresolved")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 1})
        self.assertEqual(body["trace"][2]["missing_fact"], "the operator of Polaris Relay")
        self.assertEqual(self.llm.answer_calls, 0)

    def test_answerer_can_refuse_even_after_planner_calls_first_hop_sufficient(self):
        self.llm.decision = lambda question, evidence: {
            "evidence_status": "sufficient", "missing_fact": None,
            "next_query": None, "bridge_text": None, "source_chunk_id": None}
        self.llm.answer = lambda question, evidence: {
            "status": "insufficient_evidence", "answer": None, "citation_ids": []}
        body = self.ask("Which service does Aster Telemetry Console use for audit export?").json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["stop_reason"], "model_abstained")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 2})
        self.assertEqual(body["citations"], [])

    def test_no_evidence_skips_planner_and_answerer(self):
        for document_id in self.document_ids:
            self.assertEqual(self.client.delete(f"/api/documents/{document_id}").status_code, 200)
        self.llm.decision = lambda question, evidence: self.fail("must not assess empty evidence")
        self.llm.answer = lambda question, evidence: self.fail("must not answer empty evidence")
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["stop_reason"], "no_evidence")
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 0})
        self.assertEqual(self.llm.plan_calls, 0)
        self.assertEqual(self.llm.answer_calls, 0)

    def test_inconsistent_assessment_fails_once_without_retrieval_or_answer(self):
        for decision in (
            {"evidence_status": "answered", "missing_fact": None,
             "next_query": None, "bridge_text": None, "source_chunk_id": None},
            {"evidence_status": "sufficient", "missing_fact": "operator unknown",
             "next_query": None, "bridge_text": None, "source_chunk_id": None},
            {"evidence_status": "missing", "missing_fact": None,
             "next_query": None, "bridge_text": None, "source_chunk_id": None},
            {"evidence_status": "missing", "missing_fact": "x" * 500,
             "next_query": None, "bridge_text": None, "source_chunk_id": None},
        ):
            with self.subTest(decision=decision):
                self.llm.decision = lambda question, evidence, decision=decision: decision
                self.llm.answer = lambda question, evidence: self.fail("must not answer")
                self.index.queries.clear()
                response = self.ask()
                self.assertEqual(response.status_code, 502)
                body = response.json()
                self.assertEqual(body["error_code"], "plan_output_invalid")
                self.assertEqual(body["calls"], {"retrieval": 1, "model": 1})
                self.assertEqual(len(self.index.queries), 1)
                self.assertEqual(self.llm.answer_calls, 0)

    def test_fabricated_citation_after_sufficient_assessment_cannot_succeed(self):
        self.llm.decision = lambda question, evidence: {
            "evidence_status": "sufficient", "missing_fact": None,
            "next_query": None, "bridge_text": None, "source_chunk_id": None}
        other_chunk_id = next(point["payload"]["chunk_id"] for point in self.index.points
                              if point["payload"]["document_title"] == SAMPLE["second_source"])
        for citation_id in ("not-retrieved", other_chunk_id):
            with self.subTest(citation_id=citation_id):
                self.llm.answer = lambda question, evidence, citation_id=citation_id: {
                    "status": "answered", "answer": "Invented answer", "citation_ids": [citation_id]}
                response = self.ask("Which service does Aster Telemetry Console use for audit export?")
                self.assertEqual(response.status_code, 502)
                body = response.json()
                self.assertEqual(body["error_code"], "model_output_invalid")
                self.assertEqual(body["citations"], [])
                self.assertEqual(body["calls"], {"retrieval": 1, "model": 2})
        self.assertEqual(self.llm.answer_calls, 2)


if __name__ == "__main__":
    unittest.main()
