"""DOCS-012 evidence-dependent second hop through the existing Agent API."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex
from docscope.api import create_app
from docscope.llm import ContextLimitError, OpenAIResponder


SAMPLE_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "dependency_samples"
SAMPLE = json.loads((SAMPLE_DIR / "docs012_v1.json").read_text(encoding="utf-8"))


class TwoHopIndex(FakeIndex):
    def __init__(self):
        super().__init__()
        self.queries = []

    def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
        self.queries.append(text)
        target = SAMPLE["second_source"] if SAMPLE["bridge_text"] in text else SAMPLE["first_source"]
        return [{"id": point["id"], "score": 1.0, "payload": point["payload"]}
                for point in self.points if point["payload"]["document_title"] == target
                and point["payload"]["document_id"] in ready_ids][:limit]


class EvidencePlanner:
    def __init__(self):
        self.plan_calls = []
        self.answer_calls = []
        self.proposal = None

    def plan_next(self, question, evidence):
        self.plan_calls.append((question, evidence))
        if self.proposal is not None:
            return self.proposal(question, evidence)
        return {"next_query": "Polaris Relay operator team",
                "bridge_text": SAMPLE["bridge_text"],
                "source_chunk_id": evidence[0]["chunk_id"]}

    def generate(self, question, evidence):
        self.answer_calls.append((question, evidence))
        sources = {item["document_title"]: item for item in evidence}
        if SAMPLE["second_source"] not in sources:
            return {"status": "insufficient_evidence", "answer": None, "citation_ids": []}
        return {"status": "answered", "answer": SAMPLE["answer"],
                "citation_ids": [sources[name]["chunk_id"] for name in
                                 (SAMPLE["first_source"], SAMPLE["second_source"])]}


class SecondHopApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = TwoHopIndex()
        self.llm = EvidencePlanner()
        self.client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                            index=self.index, llm=self.llm))
        self.addCleanup(self.client.close)
        for name in (SAMPLE["first_source"], SAMPLE["second_source"]):
            response = self.client.post("/api/documents", files={"file": (name, (SAMPLE_DIR / name).read_bytes())})
            self.assertEqual(response.status_code, 201)

    def run_sample(self):
        return self.client.post("/api/agent/run", json={"question": SAMPLE["question"]})

    def test_fixed_sample_second_query_uses_new_first_hop_entity_and_provenance(self):
        response = self.run_sample()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["answer"], SAMPLE["answer"])
        self.assertEqual(body["calls"], {"retrieval": 2, "model": 2})
        self.assertEqual([item["stage"] for item in body["trace"]],
                         ["plan", "retrieve", "plan_next", "retrieve", "answer"])
        first, bridge, second = body["trace"][1:4]
        self.assertEqual((first["round"], second["round"]), (1, 2))
        self.assertNotIn(SAMPLE["bridge_text"], SAMPLE["question"])
        self.assertIn(SAMPLE["bridge_text"], bridge["next_query"])
        self.assertEqual(bridge["bridge_text"], SAMPLE["bridge_text"])
        self.assertEqual(bridge["source_chunk_id"], first["evidence_ids"][0])
        self.assertEqual(self.index.queries, [SAMPLE["question"], bridge["next_query"]])
        self.assertEqual(set(body["retrieved_evidence_ids"]),
                         set(first["evidence_ids"] + second["evidence_ids"]))
        self.assertEqual({citation["chunk_id"] for citation in body["citations"]},
                         set(body["retrieved_evidence_ids"]))
        saved = self.root / "runs" / f"{body['run_id']}.json"
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8")), body)
        self.assertEqual(len(self.llm.plan_calls), 1)
        self.assertEqual(len(self.llm.answer_calls), 1)
        self.assertEqual({item["document_title"] for item in self.llm.plan_calls[0][1]},
                         {SAMPLE["first_source"]})

    def test_repeated_query_does_not_start_second_retrieval(self):
        self.llm.proposal = lambda question, evidence: {
            "next_query": question, "bridge_text": SAMPLE["bridge_text"],
            "source_chunk_id": evidence[0]["chunk_id"]}
        body = self.run_sample().json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(self.index.queries, [SAMPLE["question"]])
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 2})
        self.assertEqual(body["trace"][2]["reason"], "repeated_query")

    def test_planner_can_stop_after_first_hop_without_second_query(self):
        self.llm.proposal = lambda question, evidence: {
            "next_query": None, "bridge_text": None, "source_chunk_id": None}
        response = self.run_sample()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(self.index.queries, [SAMPLE["question"]])
        self.assertEqual(body["calls"], {"retrieval": 1, "model": 2})
        self.assertEqual(body["trace"][2]["reason"], "no_followup")
        self.assertEqual(len(self.llm.answer_calls), 1)

    def test_second_retrieval_failure_is_recorded_without_answer(self):
        original = self.index.query_hybrid

        def failing_second_retrieval(vector, text, ready_ids, limit, candidate_limit):
            if SAMPLE["bridge_text"] in text:
                self.index.queries.append(text)
                raise RuntimeError("second retrieval unavailable")
            return original(vector, text, ready_ids, limit, candidate_limit)

        self.index.query_hybrid = failing_second_retrieval
        response = self.run_sample()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error_code"], "retrieval_failed")
        self.assertEqual(body["calls"], {"retrieval": 2, "model": 1})
        self.assertEqual(body["answer"], None)
        self.assertEqual(body["citations"], [])
        self.assertEqual(self.index.queries, [SAMPLE["question"], "Polaris Relay operator team"])
        self.assertEqual(self.llm.answer_calls, [])

    def test_preplanned_or_fabricated_bridge_is_not_counted_as_evidence_driven(self):
        for proposal in (
            {"queries": [SAMPLE["question"], "Polaris Relay operator team"]},
            {"next_query": "Polaris Relay operator team", "bridge_text": SAMPLE["bridge_text"],
             "source_chunk_id": "not-a-retrieved-chunk"},
            {"next_query": "Polaris Relay operator team", "bridge_text": "East Region Operations",
             "source_chunk_id": "from-first"},
        ):
            with self.subTest(proposal=proposal):
                self.index.queries.clear()
                self.llm.answer_calls.clear()
                self.llm.proposal = lambda _, evidence, proposal=proposal: {
                    **proposal,
                    **({"source_chunk_id": evidence[0]["chunk_id"]} if proposal.get("source_chunk_id") == "from-first" else {}),
                }
                response = self.run_sample()
                self.assertEqual(response.status_code, 502)
                body = response.json()
                self.assertEqual(body["status"], "error")
                self.assertEqual(body["error_code"], "plan_output_invalid")
                self.assertEqual(body["citations"], [])
                self.assertEqual(self.index.queries, [SAMPLE["question"]])
                self.assertEqual(self.llm.answer_calls, [])

    def test_second_retrieval_has_hard_cap_even_when_no_new_evidence(self):
        original = self.index.query_hybrid

        def repeated_evidence(vector, text, ready_ids, limit, candidate_limit):
            if SAMPLE["bridge_text"] in text:
                self.index.queries.append(text)
                return original(vector, SAMPLE["question"], ready_ids, limit, candidate_limit)
            return original(vector, text, ready_ids, limit, candidate_limit)

        self.index.query_hybrid = repeated_evidence
        body = self.run_sample().json()
        self.assertEqual(body["status"], "insufficient_evidence")
        self.assertEqual(body["calls"], {"retrieval": 2, "model": 2})
        self.assertEqual(len(self.llm.plan_calls), 1)
        self.assertEqual(len(self.llm.answer_calls), 1)
        self.assertEqual(len(body["retrieved_evidence_ids"]), 1)
        self.assertEqual(body["trace"][3]["new_evidence_ids"], [])


class OpenAIPlannerTest(unittest.TestCase):
    def test_planner_and_answer_log_response_usage_even_when_incomplete(self):
        requested_limits = []

        def fake_openai(**_):
            def create(**params):
                requested_limits.append(params["max_tokens"])
                planner = params["max_tokens"] == 2048
                return SimpleNamespace(
                    model="loomy/DeepSeek-V4.1-Flash",
                    choices=[SimpleNamespace(
                        finish_reason="stop" if planner else "length",
                        message=SimpleNamespace(content=(
                            '{"next_query":null,"bridge_text":null,"source_chunk_id":null}'
                            if planner else None)))],
                    usage=SimpleNamespace(prompt_tokens=348, completion_tokens=387 if planner else 400,
                                          total_tokens=735 if planner else 748,
                                          completion_tokens_details=SimpleNamespace(
                                              reasoning_tokens=366 if planner else 400)))

            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                   close=lambda: None)

        evidence = [{"chunk_id": "first", "document_title": "notes.md", "section": "Notes",
                     "page_number": None, "chunk_text": "A relevant fact."}]
        env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "local-test-key",
               "LLM_MODEL": "group/docscope-free"}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=fake_openai)}):
            with self.assertLogs("uvicorn.error", level="INFO") as captured:
                proposal = OpenAIResponder().plan_next("Which team?", evidence)
                with self.assertRaisesRegex(ValueError, "LLM response incomplete"):
                    OpenAIResponder().generate("Which team?", evidence)
        self.assertEqual(proposal["next_query"], None)
        self.assertEqual(requested_limits, [2048, 400])
        self.assertEqual(len(captured.records), 2)
        planner_log, answer_log = (record.getMessage() for record in captured.records)
        for value in ("stage=plan_next", "finish_reason=stop", "completion_tokens=387",
                      "reasoning_tokens=366", "prompt_tokens=348", "total_tokens=735"):
            self.assertIn(value, planner_log)
        for value in ("stage=answer", "finish_reason=length", "completion_tokens=400",
                      "reasoning_tokens=400", "prompt_tokens=348", "total_tokens=748"):
            self.assertIn(value, answer_log)
        self.assertNotIn("local-test-key", "\n".join((planner_log, answer_log)))
        self.assertNotIn("A relevant fact", "\n".join((planner_log, answer_log)))

    def test_planner_budget_leaves_room_for_json_after_observed_reasoning_usage(self):
        observed_completion_tokens = 221

        def fake_openai(**_):
            def create(**params):
                complete = params["max_tokens"] >= observed_completion_tokens
                content = json.dumps({"next_query": None, "bridge_text": None,
                                      "source_chunk_id": None}) if complete else None
                return SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="stop" if complete else "length",
                    message=SimpleNamespace(content=content))])

            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                   close=lambda: None)

        evidence = [{"chunk_id": "first", "document_title": "notes.md", "section": "Notes",
                     "page_number": None, "chunk_text": "A relevant fact."}]
        env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "local-test-key",
               "LLM_MODEL": "group/docscope-free"}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=fake_openai)}):
            proposal = OpenAIResponder().plan_next("Which team?", evidence)
        self.assertEqual(proposal, {"next_query": None, "bridge_text": None,
                                    "source_chunk_id": None})

    def test_planner_uses_only_question_and_retrieved_chunks_as_untrusted_data(self):
        captured = {}

        def fake_openai(**settings):
            captured["settings"] = settings

            def create(**params):
                captured["request"] = params
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps({
                        "next_query": "Polaris Relay operator team",
                        "bridge_text": "Polaris Relay", "source_chunk_id": "first-chunk",
                    })))])

            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                   close=lambda: captured.update(closed=True))

        evidence = [{"chunk_id": "first-chunk", "document_title": SAMPLE["first_source"],
                     "section": "Aster Telemetry Console", "page_number": None,
                     "chunk_text": (SAMPLE_DIR / SAMPLE["first_source"]).read_text(encoding="utf-8")}]
        env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "local-test-key",
               "LLM_MODEL": "group/docscope-free"}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=fake_openai)}):
            proposal = OpenAIResponder().plan_next(SAMPLE["question"], evidence)
        self.assertEqual(proposal["source_chunk_id"], "first-chunk")
        self.assertEqual(captured["settings"]["max_retries"], 0)
        self.assertEqual(captured["request"]["response_format"], {"type": "json_object"})
        self.assertEqual(captured["request"]["model"], "group/docscope-free")
        self.assertTrue(captured.get("closed"))
        system, user = captured["request"]["messages"]
        self.assertIn("untrusted", system["content"].lower())
        self.assertNotIn("Aster Telemetry Console uses", system["content"])
        payload = json.loads(user["content"])
        self.assertEqual(payload["question"], SAMPLE["question"])
        self.assertEqual([item["chunk_id"] for item in payload["evidence"]], ["first-chunk"])
        self.assertNotIn(SAMPLE["answer"], user["content"])
        self.assertNotIn("local-test-key", user["content"])

    def test_planner_rejects_oversized_prompt_before_network_call(self):
        evidence = [{"chunk_id": "first", "document_title": "notes.md", "section": "Notes",
                     "page_number": None, "chunk_text": "x" * 13000}]

        def forbidden_client(**_):
            raise AssertionError("network client must not be constructed")

        env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "local-test-key",
               "LLM_MODEL": "group/docscope-free"}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=forbidden_client)}):
            with self.assertRaises(ContextLimitError):
                OpenAIResponder().plan_next("Which team?", evidence)


if __name__ == "__main__":
    unittest.main()
