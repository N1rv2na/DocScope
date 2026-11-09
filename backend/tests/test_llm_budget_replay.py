"""DOCS-023: frozen dev prompt reconstruction and honest budget accounting."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docscope.evaluation import llm_budget
from docscope.evaluation.llm_budget import (historical_summary, load_prompt_cases,
                                             paired_comparison, replay_calls, run, summarize_replay)
from docscope.llm import LLMCallError, LLMJsonResult


ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "evaluation/configs/llm_budget_dev_v1.json").read_text())


class FrozenReplayTest(unittest.TestCase):
    def test_online_default_replay_is_bounded_to_24_frozen_inputs(self):
        self.assertTrue(hasattr(llm_budget, "run_online_default"))
        seen = []

        def complete(stage, instructions, message, max_tokens):
            seen.append((stage, instructions, max_tokens))
            value = ({"evidence_status": "sufficient", "missing_fact": None,
                      "next_query": None, "bridge_text": None, "source_chunk_id": None}
                     if stage == "plan_next" else
                     {"status": "insufficient_evidence", "answer": None, "citation_ids": []})
            return LLMJsonResult(value, {"stage": stage, "max_tokens": max_tokens,
                                         "timeout_seconds": 60, "completion_tokens": 20,
                                         "reasoning_tokens": None, "elapsed_ms": 1.0})

        env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
               "LLM_MODEL": "group/docscope-free"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, env), patch(
                "docscope.evaluation.llm_budget.OpenAIResponder._complete_json", side_effect=complete):
            output = Path(directory) / "online.json"
            result = llm_budget.run_online_default(ROOT, CONFIG, output)
            self.assertEqual(len(result["rows"]), 24)
            self.assertEqual(len(seen), 24)
            self.assertEqual({budget for stage, _, budget in seen if stage == "answer"}, {2048})
            self.assertEqual({budget for stage, _, budget in seen if stage == "plan_next"}, {4096})
            self.assertTrue(all("Output one JSON object immediately" in prompt for _, prompt, _ in seen))
            self.assertEqual(result["summary"]["online_default"]["success_calls"], 24)
            self.assertEqual(result["config"]["timeout_seconds"], 60)
            self.assertTrue(output.with_suffix(".md").exists())
            self.assertNotIn("secret-test-key", output.read_text())
            with self.assertRaises(FileExistsError):
                llm_budget.run_online_default(ROOT, CONFIG, output)
            excessive = {**CONFIG, "samples": CONFIG["samples"] + [CONFIG["samples"][0]]}
            with self.assertRaises(ValueError):
                llm_budget.run_online_default(ROOT, excessive, Path(directory) / "excessive.json")
            self.assertEqual(len(seen), 24)

    def test_frozen_dev_cases_include_all_12_truncations_and_12_controls(self):
        source, cases = load_prompt_cases(ROOT, CONFIG)
        self.assertEqual(len(cases), 24)
        self.assertEqual(sum(case["historical_finish_reason"] == "length" for case in cases), 12)
        self.assertEqual(sum(case["stage"] == "plan_next" for case in cases), 6)
        self.assertEqual(len({case["sample_id"] for case in cases}), 14)
        self.assertTrue(all(set(case) == {"sample_id", "mode", "stage", "question", "evidence",
                                                 "historical_finish_reason"} for case in cases))
        self.assertTrue(all("fact" not in item and "source_document_id" not in item
                            for case in cases for item in case["evidence"]))
        for case in cases:
            self.assertTrue(case["question"])
            self.assertTrue(case["evidence"])
            self.assertTrue(all(item["chunk_text"] for item in case["evidence"]))
        summary = historical_summary(source)
        self.assertEqual(summary["total_calls"], 86)
        self.assertEqual(summary["truncated_calls"], 12)
        self.assertEqual(summary["by_stage"]["answer"]["truncated_calls"], 9)
        self.assertEqual(summary["by_stage"]["plan_next"]["truncated_calls"], 3)
        self.assertEqual(summary["by_stage"]["plan_once"]["truncated_calls"], 0)
        self.assertEqual(len(summary["truncated_samples"]), 12)

    def test_result_hash_and_sample_provenance_are_checked(self):
        with self.assertRaisesRegex(ValueError, "hash"):
            load_prompt_cases(ROOT, {**CONFIG, "source_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "dev"):
            load_prompt_cases(ROOT, {**CONFIG, "split": "test"})
        bad = {**CONFIG, "samples": [{**CONFIG["samples"][0], "historical_finish_reason": "stop"}]}
        with self.assertRaisesRegex(ValueError, "finish"):
            load_prompt_cases(ROOT, bad)

    def test_summary_keeps_unknown_usage_unknown_and_no_unpaired_causal_claim(self):
        rows = [
            {"sample_id": "x", "stage": "answer", "arm": "historical_budget_prompt",
             "status": "output_truncated", "llm_call": {"completion_tokens": 2048,
                                                   "reasoning_tokens": None, "elapsed_ms": 3.0,
                                                   "response_model": "model"}},
            {"sample_id": "x", "stage": "answer", "arm": "larger_budget_same_prompt",
             "status": "success", "llm_call": {"completion_tokens": 2300,
                                                "reasoning_tokens": 2100, "elapsed_ms": 5.0,
                                                "response_model": "model"}},
        ]
        result = summarize_replay(rows, ["historical_budget_prompt", "larger_budget_same_prompt"])
        self.assertEqual(result["historical_budget_prompt"]["unknown_reasoning_calls"], 1)
        self.assertEqual(result["historical_budget_prompt"]["known_reasoning_tokens"], 0)
        self.assertEqual(result["larger_budget_same_prompt"]["success_calls"], 1)

    def test_paired_comparison_excludes_timeout_from_token_pair_denominator(self):
        rows = [
            {"sample_id": "x", "mode": "A", "stage": "answer", "arm": "old", "status": "success",
             "llm_call": {"completion_tokens": 10, "elapsed_ms": 100}},
            {"sample_id": "x", "mode": "A", "stage": "answer", "arm": "new", "status": "success",
             "llm_call": {"completion_tokens": 15, "elapsed_ms": 120}},
            {"sample_id": "y", "mode": "E", "stage": "plan_next", "arm": "old",
             "status": "request_timeout", "llm_call": {"completion_tokens": None, "elapsed_ms": 60000}},
            {"sample_id": "y", "mode": "E", "stage": "plan_next", "arm": "new", "status": "success",
             "llm_call": {"completion_tokens": 30, "elapsed_ms": 200}},
        ]
        comparison = paired_comparison(rows, "old", "new")
        self.assertEqual(comparison["common_inputs"], 2)
        self.assertEqual(comparison["paired_success_with_known_tokens"], 1)
        self.assertEqual(comparison["completion_tokens_left"], 10)
        self.assertEqual(comparison["completion_tokens_right"], 15)

    def test_replay_validates_schema_and_citations_without_saving_generated_text(self):
        _, cases = load_prompt_cases(ROOT, CONFIG)
        answer = next(case for case in cases if case["stage"] == "answer")
        response = LLMJsonResult({"status": "answered", "answer": "secret generated text",
                                  "citation_ids": ["fabricated"]}, {"stage": "answer", "max_tokens": 2048})
        with patch("docscope.evaluation.llm_budget.OpenAIResponder._complete_json", return_value=response):
            rows = replay_calls([answer], [CONFIG["arms"][0]])
        self.assertEqual(rows[0]["status"], "answer_invalid")
        self.assertNotIn("secret generated text", json.dumps(rows))
        response = LLMJsonResult({"status": "insufficient_evidence", "answer": None,
                                  "citation_ids": [], "extra": "bad"}, {"stage": "answer"})
        with patch("docscope.evaluation.llm_budget.OpenAIResponder._complete_json", return_value=response):
            rows = replay_calls([answer], [CONFIG["arms"][0]])
        self.assertEqual(rows[0]["status"], "schema_invalid")
        with patch("docscope.evaluation.llm_budget.OpenAIResponder._complete_json",
                   side_effect=LLMCallError("output_truncated", {"reasoning_tokens": 2048,
                                                                "max_tokens": 2048})):
            rows = replay_calls([answer], [CONFIG["arms"][0]])
        self.assertEqual(rows[0]["status"], "output_truncated")
        self.assertEqual(rows[0]["llm_call"]["reasoning_tokens"], 2048)

    def test_mocked_three_arm_run_writes_only_metadata_and_refuses_overwrite(self):
        def complete(stage, instructions, message, max_tokens):
            if stage == "plan_next":
                value = {"evidence_status": "sufficient", "missing_fact": None,
                         "next_query": None, "bridge_text": None, "source_chunk_id": None}
            else:
                value = {"status": "insufficient_evidence", "answer": None, "citation_ids": []}
            return LLMJsonResult(value, {"stage": stage, "max_tokens": max_tokens,
                                         "timeout_seconds": 60, "completion_tokens": 20,
                                         "reasoning_tokens": None, "elapsed_ms": 1.0,
                                         "response_model": "group/docscope-free"})

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            env = {"LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
                   "LLM_MODEL": "group/docscope-free"}
            with patch.dict(os.environ, env), patch(
                    "docscope.evaluation.llm_budget.OpenAIResponder._complete_json", side_effect=complete):
                result = run(ROOT, CONFIG, output)
                self.assertEqual(len(result["rows"]), 72)
                self.assertTrue(output.with_suffix(".md").is_file())
                with self.assertRaises(FileExistsError):
                    run(ROOT, CONFIG, output)
            persisted = output.read_text()
            self.assertNotIn("secret-test-key", persisted)
            _, cases = load_prompt_cases(ROOT, CONFIG)
            self.assertNotIn(cases[0]["evidence"][0]["chunk_text"], persisted)
            self.assertEqual({item["status"] for item in result["rows"]}, {"success"})

    def test_run_rejects_config_that_exceeds_approved_72_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
                "LLM_MODEL": "group/docscope-free"}):
            extra = {**CONFIG, "samples": CONFIG["samples"] + [{
                "sample_id": "mhr-q-66a99ee476b80008b802", "mode": "E", "stage": "plan_next",
                "historical_finish_reason": "stop"}]}
            with patch("docscope.evaluation.llm_budget.OpenAIResponder._complete_json",
                       side_effect=AssertionError("model call must not occur")):
                with self.assertRaisesRegex(ValueError, "72"):
                    run(ROOT, extra, Path(directory) / "too-many.json")

    def test_run_rejects_arms_that_change_more_than_one_variable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "LLM_BASE_URL": "http://127.0.0.1:3425/v1", "LLM_API_KEY": "secret-test-key",
                "LLM_MODEL": "group/docscope-free"}):
            arms = [dict(arm) for arm in CONFIG["arms"]]
            arms[2]["planning_max_tokens"] = 8192
            with patch("docscope.evaluation.llm_budget.OpenAIResponder._complete_json",
                       side_effect=AssertionError("model call must not occur")):
                with self.assertRaisesRegex(ValueError, "arms"):
                    run(ROOT, {**CONFIG, "arms": arms}, Path(directory) / "unpaired.json")


if __name__ == "__main__":
    unittest.main()
