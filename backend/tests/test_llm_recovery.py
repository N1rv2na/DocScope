"""DOCS-024 bounded model request recovery and public terminal states."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APIStatusError

from backend.tests.test_agent_run import HybridIndex
from backend.tests.test_markdown_dense_api import FakeEmbedder, SAMPLE
from docscope.api import create_app
from docscope.llm import ContextLimitError, LLMCallError, LLMConfigurationError, LLMJsonResult, OpenAIResponder


def diagnostic(stage, tokens=7):
    return {"stage": stage, "requested_model": "group/docscope-free", "response_model": "model",
            "finish_reason": "stop", "prompt_tokens": 5, "completion_tokens": tokens,
            "reasoning_tokens": 3, "total_tokens": 5 + tokens, "elapsed_ms": 1.0}


class RequestClassificationTest(unittest.TestCase):
    def test_client_construction_failure_is_local_configuration_error(self):
        def broken_openai(**_):
            raise RuntimeError("private setup detail")

        env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
               "LLM_MODEL": "group/docscope-free"}
        evidence = [{"chunk_id": "one", "document_title": "doc", "section": "S", "chunk_text": "Fact."}]
        with patch.dict(os.environ, env), patch.dict(sys.modules, {
                "openai": SimpleNamespace(OpenAI=broken_openai)}), self.assertRaises(LLMConfigurationError) as caught:
            OpenAIResponder().generate("Question?", evidence)
        self.assertNotIn("private setup detail", str(caught.exception))

    def test_http_errors_are_classified_without_provider_message(self):
        for status, expected in ((429, "request_rate_limited"), (503, "request_server_error"),
                                 (401, "request_auth_failed"), (400, "request_failed")):
            with self.subTest(status=status):
                response = httpx.Response(status, request=httpx.Request("POST", "https://example.test/v1"))
                error = APIStatusError("private document text", response=response, body=None)

                def fake_openai(**_):
                    def create(**_):
                        raise error
                    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                           close=lambda: None)

                env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
                       "LLM_MODEL": "group/docscope-free"}
                with patch.dict(os.environ, env), patch.dict(sys.modules, {
                        "openai": SimpleNamespace(OpenAI=fake_openai)}), self.assertRaises(LLMCallError) as caught:
                    OpenAIResponder().generate("Question?", [{"chunk_id": "one", "document_title": "doc",
                                                                "section": "S", "chunk_text": "Fact."}])
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private document text", json.dumps(caught.exception.diagnostic))


class AgentRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.answers = []
        self.plans = []
        self.invocations = []
        self.index = HybridIndex()

        def generate(_, evidence):
            self.invocations.append("answer")
            item = self.answers.pop(0)
            if isinstance(item, Exception):
                raise item
            if item == "answer":
                item = {"status": "answered", "answer": "North Observatory.",
                        "citation_ids": [evidence[0]["chunk_id"]]}
            return LLMJsonResult(item, diagnostic("answer"))

        def plan_next(*_):
            self.invocations.append("plan_next")
            item = self.plans.pop(0)
            if isinstance(item, Exception):
                raise item
            return LLMJsonResult(item, diagnostic("plan_next"))

        self.llm = SimpleNamespace(generate=generate, plan_next=plan_next)
        self.client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                            index=self.index, llm=self.llm))
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post("/api/documents", files={
            "file": ("notes.md", SAMPLE)}).status_code, 201)
        self.good_plan = {"evidence_status": "sufficient", "missing_fact": None,
                          "next_query": None, "bridge_text": None, "source_chunk_id": None}

    def test_planner_timeout_then_success_then_answer_uses_three_requests(self):
        self.plans = [LLMCallError("request_timeout", diagnostic("plan_next")), self.good_plan]
        self.answers = ["answer"]
        response = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["calls"]["model"], 3)
        self.assertEqual(body["model_request_budget"], 3)
        self.assertEqual(body["retry_requests"], 1)
        self.assertEqual([x["error_code"] for x in body["trace"][2]["llm_attempts"]],
                         ["request_timeout", None])
        self.assertEqual(body["model_usage"]["known_completion_tokens"], 21)
        self.assertEqual(self.invocations, ["plan_next", "plan_next", "answer"])

    def test_answer_truncation_recovers_but_fabricated_citation_never_succeeds(self):
        self.plans = [self.good_plan]
        self.answers = [LLMCallError("output_truncated", diagnostic("answer", 2048)), "answer"]
        response = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["calls"]["model"], 3)
        self.assertEqual(response.json()["model_usage"]["known_completion_tokens"], 2062)
        self.plans = [self.good_plan]
        self.answers = [{"status": "answered", "answer": "Invented", "citation_ids": ["fake"]}]
        self.invocations.clear()
        bad = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(bad.status_code, 502)
        self.assertEqual(bad.json()["error_code"], "model_output_invalid")
        self.assertEqual(self.invocations, ["plan_next", "answer"])

    def test_auth_is_not_retried_and_retry_budget_exhaustion_stays_error(self):
        self.plans = [LLMCallError("request_auth_failed", diagnostic("plan_next"))]
        auth = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(auth.status_code, 503)
        self.assertEqual(auth.json()["calls"]["model"], 1)
        self.assertEqual(self.invocations, ["plan_next"])
        self.plans = [LLMCallError("json_invalid", diagnostic("plan_next")), self.good_plan]
        self.answers = [LLMCallError("request_server_error", diagnostic("answer"))]
        self.invocations.clear()
        exhausted = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(exhausted.status_code, 503)
        self.assertEqual(exhausted.json()["calls"]["model"], 3)
        self.assertEqual(exhausted.json()["trace"][-1]["recovery_stop_reason"],
                         "request_budget_exhausted")

    def test_rate_limit_server_error_and_schema_can_recover_once(self):
        for code in ("request_rate_limited", "request_server_error", "schema_invalid"):
            with self.subTest(code=code):
                self.plans = [self.good_plan]
                self.answers = [LLMCallError(code, diagnostic("answer")), "answer"]
                self.invocations.clear()
                response = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["calls"]["model"], 3)
                self.assertEqual(self.invocations, ["plan_next", "answer", "answer"])
        self.plans = [self.good_plan]
        self.answers = [{"status": "answered"}, "answer"]
        response = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["trace"][-1]["llm_attempts"][0]["error_code"],
                         "schema_invalid")

    def test_planner_missing_field_recovers_but_invalid_bridge_does_not(self):
        self.plans = [{"evidence_status": "sufficient"}, self.good_plan]
        self.answers = ["answer"]
        recovered = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["calls"]["model"], 3)
        self.assertEqual(recovered.json()["trace"][2]["llm_attempts"][0]["error_code"],
                         "schema_invalid")

        self.plans = [{"evidence_status": "missing", "missing_fact": "operator",
                       "next_query": "Not in evidence operator", "bridge_text": "Not in evidence",
                       "source_chunk_id": "fake"}]
        self.invocations.clear()
        invalid = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(invalid.status_code, 502)
        self.assertEqual(self.invocations, ["plan_next"])

    def test_source_replacement_after_timeout_blocks_retry(self):
        def replace_and_timeout(*_):
            evidence = self.index.points[0]["payload"]
            response = self.client.put("/api/documents/" + evidence["document_id"],
                                       files={"file": ("notes.md", b"# Changed\nNew fact.")})
            self.assertEqual(response.status_code, 200)
            raise LLMCallError("request_timeout", diagnostic("plan_next"))

        def plan(question, evidence):
            self.invocations.append("plan_next")
            return replace_and_timeout(question, evidence)

        # A fresh app captures the injected Planner callable.
        llm = SimpleNamespace(generate=self.llm.generate, plan_next=plan)
        client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                       index=self.index, llm=llm))
        self.addCleanup(client.close)
        response = client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error_code"], "source_changed")
        self.assertEqual(response.json()["calls"]["model"], 1)
        self.assertEqual(self.invocations, ["plan_next"])

    def test_local_context_and_configuration_fail_before_remote_request(self):
        for error, expected_status, expected_code in (
                (ContextLimitError("too long"), 422, "context_limit_exceeded"),
                (LLMConfigurationError("missing key"), 503, "model_failed")):
            with self.subTest(error=type(error).__name__):
                self.plans = [self.good_plan]
                self.answers = [error]
                response = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error_code"], expected_code)
                self.assertEqual(response.json()["calls"]["model"], 1)
                self.assertEqual(response.json()["trace"][-1]["llm_attempts"], [])

    def test_extra_request_can_be_disabled_at_api_startup(self):
        with patch.dict(os.environ, {"DOCSCOPE_AGENT_EXTRA_MODEL_REQUESTS": "0"}):
            client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                           index=self.index, llm=self.llm))
        self.addCleanup(client.close)
        self.plans = [LLMCallError("request_timeout", diagnostic("plan_next"))]
        response = client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["model_request_budget"], 2)
        self.assertEqual(response.json()["calls"]["model"], 1)
        self.assertEqual(self.invocations, ["plan_next"])

    def test_sse_and_http_share_recovery_terminal_semantics(self):
        self.plans = [self.good_plan]
        self.answers = [LLMCallError("json_invalid", diagnostic("answer")), "answer"]
        response = self.client.post("/api/agent/run/stream", json={"question": "Who built Orion?"})
        events = [(block.splitlines()[0].removeprefix("event: "),
                   json.loads(block.splitlines()[1].removeprefix("data: ")))
                  for block in response.text.strip().split("\n\n")]
        self.assertEqual(events[-1][0], "done")
        self.assertEqual([kind for kind, _ in events].count("done"), 1)
        self.assertEqual(events[-1][1]["status"], "answered")
        saved = json.loads((self.root / "runs" / f"{events[-1][1]['run_id']}.json").read_text())
        self.assertEqual(saved["calls"]["model"], 3)
        self.assertEqual(saved["retry_requests"], 1)
        self.plans = [self.good_plan]
        self.answers = [LLMCallError("request_auth_failed", diagnostic("answer"))]
        http = self.client.post("/api/agent/run", json={"question": "Who built Orion?"})
        self.assertEqual(http.status_code, 503)
        self.plans = [self.good_plan]
        self.answers = [LLMCallError("request_auth_failed", diagnostic("answer"))]
        streamed = self.client.post("/api/agent/run/stream", json={"question": "Who built Orion?"})
        events = [(block.splitlines()[0].removeprefix("event: "),
                   json.loads(block.splitlines()[1].removeprefix("data: ")))
                  for block in streamed.text.strip().split("\n\n")]
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["error_code"], http.json()["error_code"])
        self.assertNotIn("token", [kind for kind, _ in events])
        self.assertNotIn("done", [kind for kind, _ in events])
        saved = json.loads((self.root / "runs" / f"{events[-1][1]['run_id']}.json").read_text())
        self.assertEqual(saved["status"], http.json()["status"])
        self.assertEqual(saved["calls"], http.json()["calls"])


if __name__ == "__main__":
    unittest.main()
