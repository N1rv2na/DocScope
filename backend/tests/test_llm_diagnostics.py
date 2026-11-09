"""DOCS-022: classify model failures without persisting prompts or provider errors."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.test_agent_run import HybridIndex
from backend.tests.test_markdown_dense_api import FakeEmbedder, SAMPLE
from docscope.api import create_app
from docscope.llm import LLMCallError, LLMJsonResult, OpenAIResponder


EVIDENCE = [{"chunk_id": "first", "document_title": "notes.md", "section": "Notes",
             "page_number": None, "chunk_text": "Public fact."}]
ENV = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
       "LLM_MODEL": "group/docscope-free"}


class ModelDiagnosticsTest(unittest.TestCase):
    def invoke(self, response=None, error=None, stage="answer"):
        def fake_openai(**_):
            def create(**_):
                if error is not None:
                    raise error
                return response
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                   close=lambda: None)
        with patch.dict(os.environ, ENV), patch.dict(sys.modules, {
            "openai": SimpleNamespace(OpenAI=fake_openai)}):
            responder = OpenAIResponder()
            return (responder.generate if stage == "answer" else responder.plan_next)("Question?", EVIDENCE)

    @staticmethod
    def response(content, finish="stop", usage=True):
        return SimpleNamespace(model="actual/model", choices=[SimpleNamespace(
            finish_reason=finish, message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20,
                                  completion_tokens_details=SimpleNamespace(reasoning_tokens=5)) if usage else None)

    def test_success_has_stage_model_usage_and_latency_without_prompt(self):
        result = self.invoke(self.response('{"status":"insufficient_evidence"}'))
        self.assertIsInstance(result, LLMJsonResult)
        diag = result.diagnostic
        self.assertEqual(diag["stage"], "answer")
        self.assertEqual(diag["requested_model"], ENV["LLM_MODEL"])
        self.assertEqual(diag["response_model"], "actual/model")
        self.assertEqual(diag["finish_reason"], "stop")
        self.assertEqual(diag["prompt_tokens"], 12)
        self.assertEqual(diag["reasoning_tokens"], 5)
        self.assertGreaterEqual(diag["elapsed_ms"], 0)
        self.assertNotIn("secret-test-key", json.dumps(diag))

    def test_failures_are_distinct_and_unknown_usage_is_not_invented(self):
        for response, error, expected in [
            (self.response(None, "length"), None, "output_truncated"),
            (self.response("{bad"), None, "json_invalid"),
            (self.response("[]"), None, "schema_invalid"),
            (None, TimeoutError("secret-test-key private prompt"), "request_timeout"),
            (None, RuntimeError("secret-test-key private prompt"), "request_failed"),
        ]:
            for stage, expected_stage in (("answer", "answer"), ("planner", "plan_next")):
                with self.subTest(expected=expected, stage=stage), self.assertRaises(LLMCallError) as caught:
                    self.invoke(response, error, stage=stage)
                exc = caught.exception
                self.assertEqual(exc.code, expected)
                self.assertEqual(exc.diagnostic["stage"], expected_stage)
                self.assertGreaterEqual(exc.diagnostic["elapsed_ms"], 0)
                if error is not None:
                    self.assertIsNone(exc.diagnostic["response_model"])
                    self.assertIsNone(exc.diagnostic["completion_tokens"])
                self.assertNotIn("secret-test-key", str(exc) + json.dumps(exc.diagnostic))

    def test_openai_sdk_timeout_is_classified_as_timeout(self):
        import httpx
        from openai import APITimeoutError

        with self.assertRaises(LLMCallError) as caught:
            self.invoke(error=APITimeoutError(request=httpx.Request("POST", "http://127.0.0.1/v1")))
        self.assertEqual(caught.exception.code, "request_timeout")

    def test_missing_usage_and_unrecognized_finish_reason_are_not_called_truncation(self):
        result = self.invoke(self.response('{"status":"insufficient_evidence"}', usage=False))
        self.assertIsNone(result.diagnostic["prompt_tokens"])
        self.assertIsNone(result.diagnostic["reasoning_tokens"])
        with self.assertRaises(LLMCallError) as caught:
            self.invoke(self.response(None, "content_filter", usage=False))
        self.assertEqual(caught.exception.code, "response_incomplete")
        self.assertIsNone(caught.exception.diagnostic["total_tokens"])


class RunDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.llm = SimpleNamespace()
        self.llm.answer = lambda question, evidence: {"status": "insufficient_evidence",
                                                      "answer": None, "citation_ids": []}
        self.llm.proposal = lambda question, evidence: {"evidence_status": "sufficient",
                                                       "missing_fact": None, "next_query": None,
                                                       "bridge_text": None, "source_chunk_id": None}
        self.llm.generate = lambda question, evidence: self.llm.answer(question, evidence)
        self.llm.plan_next = lambda question, evidence: self.llm.proposal(question, evidence)
        self.client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                            index=HybridIndex(), llm=self.llm))
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post("/api/documents", files={
            "file": ("notes.md", SAMPLE)}).status_code, 201)

    def ask(self):
        response = self.client.post("/api/agent/run", json={"question": "Which observatory?"})
        body = response.json()
        saved = json.loads((self.root / "runs" / f"{body['run_id']}.json").read_text())
        self.assertEqual(body, saved)
        return response, body

    def test_planner_and_answer_typed_failures_keep_public_codes(self):
        diagnostic = {"stage": "plan_next", "requested_model": "model", "response_model": None,
                      "finish_reason": "length", "prompt_tokens": 12, "completion_tokens": 8,
                      "total_tokens": 20, "reasoning_tokens": 8, "elapsed_ms": 4.0}
        for stage, code, public, status in [
            ("plan_next", "output_truncated", "plan_output_invalid", 502),
            ("answer", "request_timeout", "model_failed", 503),
        ]:
            with self.subTest(stage=stage):
                def fail(*_):
                    raise LLMCallError(code, {**diagnostic, "stage": stage})
                setattr(self.llm, "proposal" if stage == "plan_next" else "answer", fail)
                if stage == "answer":
                    self.llm.proposal = lambda *_: {"evidence_status": "sufficient",
                                                   "missing_fact": None, "next_query": None,
                                                   "bridge_text": None, "source_chunk_id": None}
                response, body = self.ask()
                self.assertEqual(response.status_code, status)
                self.assertEqual(body["error_code"], public)
                self.assertEqual(body["trace"][-1]["llm_error_code"], code)
                self.assertEqual(body["trace"][-1]["llm_call"]["stage"], stage)

    def test_schema_failure_is_separate_from_invalid_citation_and_success_is_traced(self):
        diag = {"stage": "answer", "requested_model": "model", "response_model": "actual/model",
                "finish_reason": "stop", "prompt_tokens": 4, "completion_tokens": 5,
                "total_tokens": 9, "reasoning_tokens": None, "elapsed_ms": 1.0}
        self.llm.answer = lambda *_: LLMJsonResult({"status": "answered"}, diag)
        response, body = self.ask()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["trace"][-1]["llm_error_code"], "schema_invalid")
        self.assertEqual(body["trace"][-1]["llm_call"], diag)

        self.llm.answer = lambda *_: LLMJsonResult({"status": "answered", "answer": "invented",
                                                   "citation_ids": ["not-in-evidence"]}, diag)
        response, body = self.ask()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["trace"][-1]["llm_error_code"], "answer_invalid")

        self.llm.answer = lambda *_: LLMJsonResult({"status": "insufficient_evidence",
                                                   "answer": None, "citation_ids": []}, diag)
        response, body = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["trace"][-1]["llm_call"], diag)

    def test_successful_planner_call_is_recorded_in_trace(self):
        diag = {"stage": "plan_next", "requested_model": "model", "response_model": "actual/model",
                "finish_reason": "stop", "prompt_tokens": 3, "completion_tokens": 4,
                "reasoning_tokens": 2, "total_tokens": 7, "elapsed_ms": 6.0}
        self.llm.proposal = lambda *_: LLMJsonResult({"evidence_status": "sufficient",
                                                      "missing_fact": None, "next_query": None,
                                                      "bridge_text": None, "source_chunk_id": None}, diag)
        response, body = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["trace"][2]["llm_call"], diag)

    def test_dense_chat_preserves_http_failure_status_for_classified_request_errors(self):
        for code, expected_status, public_code in [
            ("request_timeout", 503, "model_failed"),
            ("json_invalid", 502, "model_output_invalid"),
        ]:
            with self.subTest(code=code):
                self.llm.answer = lambda *_, code=code: (_ for _ in ()).throw(
                    LLMCallError(code, {"stage": "answer", "requested_model": "model"}))
                response = self.client.post("/api/chat", json={"question": "Which observatory?"})
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error_code"], public_code)

    def test_provider_message_and_schema_input_never_reach_logs_or_saved_run(self):
        marker = "secret-test-key private-document-content"
        self.llm.answer = lambda *_: LLMJsonResult({"status": marker, "answer": None,
                                                   "citation_ids": []}, {"stage": "answer"})
        with self.assertLogs("docscope.agent_run", level="WARNING") as captured:
            response, body = self.ask()
        self.assertEqual(response.status_code, 502)
        persisted = (self.root / "runs" / f"{body['run_id']}.json").read_text()
        self.assertNotIn(marker, persisted + "\n".join(captured.output))

        def fake_openai(**_):
            def create(**_):
                raise RuntimeError(marker)
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                   close=lambda: None)
        self.llm.answer = OpenAIResponder().generate
        with patch.dict(os.environ, ENV), patch.dict(sys.modules, {
            "openai": SimpleNamespace(OpenAI=fake_openai)}), self.assertLogs(
                "uvicorn.error", level="WARNING") as llm_logs:
            response, body = self.ask()
        self.assertEqual(response.status_code, 503)
        persisted = (self.root / "runs" / f"{body['run_id']}.json").read_text()
        self.assertNotIn(marker, persisted + "\n".join(llm_logs.output))


if __name__ == "__main__":
    unittest.main()
