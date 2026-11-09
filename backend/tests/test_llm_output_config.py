"""DOCS-023 stage budgets, request timeouts, and structured-output contract."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from docscope.api import ModelAnswer, validated_answer
from docscope.llm import OpenAIResponder, PLANNER_INSTRUCTIONS, SYSTEM_INSTRUCTIONS


EVIDENCE = [{"chunk_id": "source-1", "document_id": "doc-1", "document_title": "notes.md",
             "source_path": "documents/doc-1.md", "page_number": None, "section": "Notes",
             "line_start": 1, "chunk_text": "The North Observatory built Orion.", "index_version": 1}]
ENV = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "local-test-key",
       "LLM_MODEL": "group/docscope-free"}


class OutputConfigTest(unittest.TestCase):
    def call(self, stage: str, overrides: dict[str, str] | None = None):
        observed = {}

        def fake_openai(**kwargs):
            observed["client"] = kwargs

            def create(**request):
                observed["request"] = request
                content = ('{"evidence_status":"sufficient","missing_fact":null,"next_query":null,'
                           '"bridge_text":null,"source_chunk_id":null}' if stage == "plan_next"
                           else '{"status":"insufficient_evidence","answer":null,"citation_ids":[]}')
                return SimpleNamespace(model="actual/model", choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=content))], usage=None)

            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                   close=lambda: None)

        keys = ("LLM_ANSWER_MAX_TOKENS", "LLM_PLANNER_MAX_TOKENS", "LLM_ANSWER_TIMEOUT_SECONDS",
                "LLM_PLANNER_TIMEOUT_SECONDS")
        with patch.dict(os.environ, {**ENV, **{key: "" for key in keys}, **(overrides or {})}), patch.dict(
                sys.modules, {"openai": SimpleNamespace(OpenAI=fake_openai)}):
            result = (OpenAIResponder().plan_next if stage == "plan_next" else
                      OpenAIResponder().generate)("Which observatory?", EVIDENCE)
        return result, observed

    def test_default_and_effective_stage_settings_are_in_call_diagnostics(self):
        for stage, budget in (("answer", 2048), ("plan_next", 4096)):
            with self.subTest(stage=stage):
                result, observed = self.call(stage)
                self.assertEqual(observed["request"]["max_tokens"], budget)
                self.assertEqual(observed["client"]["timeout"], 60)
                self.assertEqual(result.diagnostic["max_tokens"], budget)
                self.assertEqual(result.diagnostic["timeout_seconds"], 60)
                self.assertEqual(observed["client"]["max_retries"], 0)

    def test_independent_stage_overrides_do_not_change_other_stage(self):
        overrides = {"LLM_ANSWER_MAX_TOKENS": "3072", "LLM_PLANNER_MAX_TOKENS": "5120",
                     "LLM_ANSWER_TIMEOUT_SECONDS": "45", "LLM_PLANNER_TIMEOUT_SECONDS": "60"}
        for stage, budget, timeout in (("answer", 3072, 45), ("plan_next", 5120, 60)):
            with self.subTest(stage=stage):
                result, observed = self.call(stage, overrides)
                self.assertEqual(observed["request"]["max_tokens"], budget)
                self.assertEqual(observed["client"]["timeout"], timeout)
                self.assertEqual(result.diagnostic["max_tokens"], budget)
                self.assertEqual(result.diagnostic["timeout_seconds"], timeout)
        result, observed = self.call("answer", {"LLM_PLANNER_MAX_TOKENS": "5120",
                                                "LLM_PLANNER_TIMEOUT_SECONDS": "60"})
        self.assertEqual((result.diagnostic["max_tokens"], result.diagnostic["timeout_seconds"]), (2048, 60))

    def test_invalid_or_out_of_bounds_configuration_fails_before_request(self):
        for key, invalid in (("LLM_ANSWER_MAX_TOKENS", "0"), ("LLM_ANSWER_MAX_TOKENS", "8193"),
                             ("LLM_ANSWER_MAX_TOKENS", "2.5"), ("LLM_ANSWER_TIMEOUT_SECONDS", "0"),
                             ("LLM_PLANNER_MAX_TOKENS", "-1"), ("LLM_PLANNER_TIMEOUT_SECONDS", "121")):
            stage = "plan_next" if "PLANNER" in key else "answer"
            with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(RuntimeError, key):
                self.call(stage, {key: invalid})

    def test_response_format_and_grounding_contract_survive_prompt_change(self):
        result, observed = self.call("answer")
        self.assertEqual(observed["request"]["response_format"], {"type": "json_object"})
        self.assertIn("only the supplied evidence", SYSTEM_INSTRUCTIONS)
        self.assertIn("citation", SYSTEM_INSTRUCTIONS)
        self.assertIn("one JSON object", SYSTEM_INSTRUCTIONS)
        self.assertIn("untrusted", PLANNER_INSTRUCTIONS)
        self.assertEqual(validated_answer(ModelAnswer.model_validate(result), EVIDENCE)["status"],
                         "insufficient_evidence")
        with self.assertRaises(ValidationError):
            ModelAnswer.model_validate({"status": "answered", "answer": "North Observatory",
                                        "citation_ids": ["source-1"], "debug": "private reasoning"})
        with self.assertRaises(ValueError):
            validated_answer(ModelAnswer.model_validate({"status": "answered", "answer": "invented",
                                                         "citation_ids": ["fabricated"]}), EVIDENCE)


if __name__ == "__main__":
    unittest.main()
