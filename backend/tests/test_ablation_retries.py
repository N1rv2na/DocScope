"""Versioned A–E evaluation recovery contract without remote model calls."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docscope.evaluation.ablation import render_report, summarize_comparison, validate_config
from docscope.evaluation.run_ablation import run_mode
from docscope.evaluation.run_agent_answer import _observed_client
from docscope.llm import LLMCallError


ROOT = Path(__file__).resolve().parents[2]


class Scorer:
    def score(self, question, texts):
        return [1.0 for _ in texts]


class Responder:
    def __init__(self):
        self.planner_outputs = []
        self.answer_outputs = []
        self.planner_inputs = []
        self.answer_calls = 0

    def _complete_json(self, stage, instructions, message, max_tokens):
        self.planner_inputs.append(json.loads(message))
        value = self.planner_outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def plan_next(self, question, evidence):
        return {"evidence_status": "sufficient", "missing_fact": None,
                "next_query": None, "bridge_text": None, "source_chunk_id": None}

    def generate(self, question, evidence):
        self.answer_calls += 1
        value = self.answer_outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def search(query, mode):
    return {"points": [{"score": 1.0, "payload": {
        "document_id": "doc", "chunk_id": "chunk", "document_title": "notes.md",
        "source_path": "notes.md", "page_number": None, "section": "Notes",
        "line_start": 1, "chunk_text": "The North Observatory built Orion.",
        "index_version": 1}}],
        "candidate_count": 1, "elapsed_seconds": 0.001, "candidates": []}


ANSWER = {"status": "answered", "answer": "North Observatory.", "citation_ids": ["chunk"]}


class AblationRecoveryTest(unittest.TestCase):
    def test_new_config_freezes_one_extra_request_without_changing_subset(self):
        v3 = json.loads((ROOT / "evaluation/configs/ablation_dev_v3.json").read_text())
        v4 = json.loads((ROOT / "evaluation/configs/ablation_dev_v4.json").read_text())
        answer = json.loads((ROOT / "evaluation/configs/agent_answer_dev_v1.json").read_text())
        rerank = json.loads((ROOT / "evaluation/configs/rerank_dev_v1.json").read_text())
        validate_config(v4, answer, rerank)
        self.assertEqual(v4["extra_model_requests"], 1)
        self.assertEqual({k: value for k, value in v4.items() if k != "extra_model_requests"}, v3)
        with self.assertRaisesRegex(ValueError, "extra model requests"):
            validate_config({**v4, "extra_model_requests": 2}, answer, rerank)

    def test_d_planner_timeout_recovers_and_preserves_question_only_planning(self):
        responder = Responder()
        responder.planner_outputs = [LLMCallError("request_timeout", {}),
                                     {"second_query": "Who built Orion?"}]
        responder.answer_outputs = [ANSWER]
        result = run_mode("D", "Orion builder", search, responder, Scorer(), 5,
                          extra_model_requests=1)
        run = result["run"]
        self.assertEqual(run["status"], "answered")
        self.assertEqual(run["calls"], {"retrieval": 2, "model": 3})
        self.assertEqual(run["retry_requests"], 1)
        self.assertEqual(run["model_request_budget"], 3)
        self.assertEqual(responder.planner_inputs, [{"question": "Orion builder"}] * 2)
        self.assertEqual([step["stage"] for step in run["trace"]],
                         ["plan_once", "retrieve", "retrieve", "answer"])
        self.assertEqual([attempt["error_code"] for attempt in run["trace"][0]["llm_attempts"]],
                         ["request_timeout", None])

    def test_d_answer_truncation_recovers_but_invalid_citation_does_not(self):
        responder = Responder()
        responder.planner_outputs = [{"second_query": "Who built Orion?"}]
        responder.answer_outputs = [LLMCallError("output_truncated", {}), ANSWER]
        run = run_mode("D", "Orion builder", search, responder, Scorer(), 5,
                       extra_model_requests=1)["run"]
        self.assertEqual(run["status"], "answered")
        self.assertEqual(run["calls"]["model"], 3)
        self.assertEqual(run["retry_requests"], 1)

        responder = Responder()
        responder.planner_outputs = [{"second_query": "Who built Orion?"}]
        responder.answer_outputs = [{**ANSWER, "citation_ids": ["fabricated"]}, ANSWER]
        run = run_mode("D", "Orion builder", search, responder, Scorer(), 5,
                       extra_model_requests=1)["run"]
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["calls"]["model"], 2)
        self.assertEqual(run["retry_requests"], 0)
        self.assertEqual(responder.answer_calls, 1)

    def test_d_schema_retry_preserves_answer_slot_and_auth_is_not_retried(self):
        responder = Responder()
        responder.planner_outputs = [{"not_second_query": "wrong shape"},
                                     {"second_query": "Who built Orion?"}]
        responder.answer_outputs = [LLMCallError("request_timeout", {}), ANSWER]
        run = run_mode("D", "Orion builder", search, responder, Scorer(), 5,
                       extra_model_requests=1)["run"]
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["calls"]["model"], 3)
        self.assertEqual(run["retry_requests"], 1)
        self.assertEqual(responder.answer_calls, 1)
        self.assertEqual(run["trace"][0]["llm_attempts"][0]["error_code"], "schema_invalid")

        responder = Responder()
        responder.planner_outputs = [LLMCallError("request_auth_failed", {}),
                                     {"second_query": "Who built Orion?"}]
        run = run_mode("D", "Orion builder", search, responder, Scorer(), 5,
                       extra_model_requests=1)["run"]
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["calls"]["model"], 1)
        self.assertEqual(run["retry_requests"], 0)
        self.assertEqual(len(responder.planner_inputs), 1)

    def test_non_d_modes_receive_the_same_one_extra_request_policy(self):
        for mode in ("A", "B", "C", "E"):
            with self.subTest(mode=mode):
                responder = Responder()
                responder.answer_outputs = [LLMCallError("json_invalid", {}), ANSWER]
                run = run_mode(mode, "Orion builder", search, responder, Scorer(), 5,
                               extra_model_requests=1)["run"]
                self.assertEqual(run["status"], "answered")
                self.assertEqual(run["calls"]["model"], 3 if mode == "E" else 2)
                self.assertEqual(run["retry_requests"], 1)

    def test_new_report_discloses_retry_budget_instead_of_old_two_request_claim(self):
        v4 = json.loads((ROOT / "evaluation/configs/ablation_dev_v4.json").read_text())
        report = render_report({"config": v4, "corpus_count": 609, "sample_count": 0,
                                "summary": summarize_comparison([], []), "rows": [],
                                "run_window_utc": {"start": "s", "end": "e"}})
        self.assertIn("最多一次额外请求", report)
        self.assertIn("最多 3 次实际模型请求", report)

    def test_global_request_cap_stops_before_network_call(self):
        sent = []
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **request: sent.append(request))))
        recorded = []
        with patch('openai.OpenAI', return_value=fake):
            client = _observed_client(recorded, max_calls=0)()
            with self.assertRaisesRegex(RuntimeError, "request cap"):
                client.chat.completions.create(model='test')
        self.assertEqual(sent, [])
        self.assertEqual(recorded, [])


if __name__ == "__main__":
    unittest.main()
