"""DOCS-019 isolated, fixed-sample three-arm memory comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from docscope.api import create_app
from docscope.embedding import LocalEmbedder
from docscope.llm import LLMConfigurationError, OpenAIResponder
from docscope.memory import (MAX_RECENT_TURNS, MAX_SUMMARY_BYTES,
                             SUMMARY_TRIGGER_ESTIMATED_TOKENS)
from docscope.qdrant_index import QdrantIndex


ARMS = ("question_only", "window_rewrite", "window_summary_rewrite")


def score_turn(turn: dict, query: str | None, status: str | None,
               answer: str | None, sections: list[str]) -> dict:
    expected_entity = turn.get("expected_query_entity")
    forbidden_entity = turn.get("forbidden_query_entity")
    answer_text = answer or ""
    fact = turn["expected_fact"]
    fact_present = fact.casefold() in answer_text.casefold()
    negated_fact = bool(re.search(r"\b(?:not|never|no|isn't|wasn't)\b.{0,20}"
                                  + re.escape(fact), answer_text, re.IGNORECASE))
    conflicting_year = bool(re.fullmatch(r"\d{4}", fact) and any(
        year != fact for year in re.findall(r"\b\d{4}\b", answer_text)))
    return {
        "answer_correct": status == "answered" and fact_present and not negated_fact and not conflicting_year,
        "citation_correct": status == "answered" and any(
            turn["expected_section"].casefold() in section.casefold() for section in sections),
        "query_entity_correct": (expected_entity.casefold() in (query or "").casefold()
                                 if expected_entity else None),
        "history_polluted": (forbidden_entity.casefold() in (query or "").casefold()
                             if forbidden_entity else None),
        "answer_history_polluted": (forbidden_entity.casefold() in answer_text.casefold()
                                    if forbidden_entity else None),
    }


def summarize_rows(rows: list[dict]) -> dict:
    result = {}
    for arm in ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        def metric(name: str, positive: bool = True) -> dict:
            values = [row[name] for row in selected if row.get(name) is not None]
            return {"count": sum(value is positive for value in values),
                    "denominator": len(values)}
        result[arm] = {
            "turns": len(selected),
            "answered": {"count": sum(row.get("status") == "answered" for row in selected),
                         "denominator": len(selected)},
            "answer_correct": metric("answer_correct"),
            "citation_correct": metric("citation_correct"),
            "query_entity_correct": metric("query_entity_correct"),
            "history_polluted": metric("history_polluted"),
            "answer_history_polluted": metric("answer_history_polluted"),
            "summary_completed": sum(row.get("summary_stage") == "completed" for row in selected),
            "summary_failed": sum(row.get("summary_stage") == "failed" for row in selected),
            "remote_requests": sum(row.get("request_count", 0) for row in selected),
        }
    return result


def _events(response) -> list[tuple[str, dict]]:
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "):
            result.append((lines[0][7:], json.loads(lines[1][6:])))
    return result


def agent_diagnostics(root: Path, thread_id: str | None, run_id: str | None) -> list[dict]:
    """Extract only safe call metadata from the persisted Agent Run or checkpoint."""
    if not run_id:
        return []
    if thread_id:
        with SqliteSaver.from_conn_string(str(root / "checkpoints.sqlite3")) as saver:
            checkpoint = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        state = checkpoint.checkpoint["channel_values"] if checkpoint else {}
    else:
        record_path = root / "runs" / f"{run_id}.json"
        state = json.loads(record_path.read_text()) if record_path.exists() else {}
    if state.get("run_id") != run_id:
        return []
    return [attempt["llm_call"] for step in state.get("trace", [])
            for attempt in step.get("llm_attempts", []) if attempt.get("llm_call")]


class BoundedResponder(OpenAIResponder):
    """Single-process hard cap shared by all arms; SDK retries are disabled upstream."""

    def __init__(self, cap: int):
        self.cap = cap
        self.requests = 0

    def _complete_json(self, stage: str, system_message: str,
                       user_message: str, max_tokens: int) -> dict:
        if self.requests >= self.cap:
            raise LLMConfigurationError("DOCS-019 remote request cap reached")
        self.requests += 1
        return OpenAIResponder._complete_json(stage, system_message, user_message, max_tokens)


class WindowOnlyResponder:
    """Evaluation arm uses the same model without exposing a summarize method."""

    def __init__(self, model: BoundedResponder):
        self.generate = model.generate
        self.plan_next = model.plan_next
        self.rewrite_question = model.rewrite_question


def _row(turn: dict, dialogue_id: str, arm: str, turn_number: int,
         wire: list[tuple[str, dict]], requests: int,
         persisted_calls: list[dict]) -> dict:
    stages = [payload for kind, payload in wire if kind == "stage"]
    terminal_kind, terminal = wire[-1] if wire else ("missing", {})
    query = next((item.get("query") for item in stages if item.get("stage") == "plan"), None)
    citations = [payload["citation"] for kind, payload in wire if kind == "citation"]
    sections = [item.get("section") or "" for item in citations]
    diagnostics = []
    for stage in stages:
        if stage.get("llm_call"):
            diagnostics.append(stage["llm_call"])
        diagnostics.extend(attempt.get("llm_call") for attempt in stage.get("llm_attempts", [])
                           if attempt.get("llm_call"))
    diagnostics.extend(persisted_calls)
    summary_stage = next((item.get("status") for item in stages
                          if item.get("stage") == "summary"), None)
    return {"arm": arm, "dialogue_id": dialogue_id, "turn": turn_number,
            "question": turn["question"], "expected_fact": turn["expected_fact"],
            "expected_section": turn["expected_section"],
            "expected_query_entity": turn.get("expected_query_entity"),
            "forbidden_query_entity": turn.get("forbidden_query_entity"),
            "query": query, "terminal_event": terminal_kind, "status": terminal.get("status"),
            "error_code": terminal.get("error_code"), "answer": terminal.get("answer"),
            "citation_ids": [item.get("chunk_id") for item in citations],
            "citation_sections": sections, "summary_stage": summary_stage,
            "llm_calls": diagnostics, "request_count": requests,
            **score_turn(turn, query, terminal.get("status"), terminal.get("answer"), sections)}


def run(config_path: Path, output_path: Path, qdrant_url: str) -> dict:
    config = json.loads(config_path.read_text())
    if tuple(config["arms"]) != ARMS or config["max_remote_requests"] != 112:
        raise ValueError("DOCS-019 frozen arms or request cap changed")
    if (config["summary_trigger_estimated_tokens"] != SUMMARY_TRIGGER_ESTIMATED_TOKENS
            or config["recent_turns"] != MAX_RECENT_TURNS
            or config["summary_max_bytes"] != MAX_SUMMARY_BYTES):
        raise ValueError("DOCS-019 frozen memory settings changed")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    root = Path.cwd()
    sample = json.loads((root / config["sample_file"]).read_text())
    sample_hash = hashlib.sha256((root / config["sample_file"]).read_bytes()).hexdigest()
    source_hash = hashlib.sha256((root / sample["source_file"]).read_bytes()).hexdigest()
    if sample_hash != config["sample_sha256"] or source_hash != config["source_sha256"]:
        raise ValueError("frozen memory sample or source content changed")
    if os.getenv("LLM_MODEL") != config["model"]:
        raise ValueError("LLM_MODEL differs from frozen evaluation model")
    if os.getenv("DOCSCOPE_AGENT_EXTRA_MODEL_REQUESTS", "1") != "0":
        raise ValueError("set DOCSCOPE_AGENT_EXTRA_MODEL_REQUESTS=0 for this comparison")
    responder = BoundedResponder(config["max_remote_requests"])
    index = QdrantIndex(qdrant_url, collection="docscope_memory_" + uuid.uuid4().hex[:12])
    embedder = LocalEmbedder()
    result = {"config": config, "sample_id": sample["sample_id"],
              "sample_sha256": sample_hash, "source_sha256": source_hash,
              "collection": index.collection, "actual_channel": "unknown",
              "rows": [], "status": "running", "remote_requests": 0}

    def write_result():
        result["remote_requests"] = responder.requests
        result["summary"] = summarize_rows(result["rows"])
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(output_path)

    try:
        with tempfile.TemporaryDirectory(prefix="docscope_memory_eval_") as data_dir:
            source = root / sample["source_file"]
            for arm in ARMS:
                llm = WindowOnlyResponder(responder) if arm == "window_rewrite" else responder
                with TestClient(create_app(data_dir=Path(data_dir), embedder=embedder,
                                           index=index, llm=llm)) as client:
                    if arm == ARMS[0]:
                        uploaded = client.post("/api/documents", files={
                            "file": (source.name, source.read_bytes(), "text/markdown")})
                        if uploaded.status_code != 201 or uploaded.json().get("status") != "ready":
                            raise RuntimeError("frozen sample upload failed")
                    for dialogue in sample["dialogues"]:
                        thread_id = (client.post("/api/conversations").json()["thread_id"]
                                     if arm != "question_only" else None)
                        for number, turn in enumerate(dialogue["turns"], 1):
                            if responder.requests >= responder.cap:
                                result["status"] = "request_cap_reached"
                                write_result()
                                return result
                            endpoint = (f"/api/conversations/{thread_id}/runs/stream" if thread_id
                                        else "/api/agent/run/stream")
                            before = responder.requests
                            wire = _events(client.post(endpoint, json={"question": turn["question"]}))
                            run_id = wire[-1][1].get("run_id") if wire else None
                            persisted_calls = agent_diagnostics(Path(data_dir), thread_id, run_id)
                            result["rows"].append(_row(turn, dialogue["id"], arm, number,
                                                       wire, responder.requests - before,
                                                       persisted_calls))
                            write_result()
            result["status"] = "completed"
    except Exception as error:
        result["status"] = "failed"
        result["failure_type"] = type(error).__name__
        raise
    finally:
        write_result()
        try:
            index._request("DELETE", f"/collections/{index.collection}")
        except Exception:
            pass
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("evaluation/configs/memory_dev_v1.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6337")
    args = parser.parse_args()
    result = run(args.config, args.output, args.qdrant_url)
    print(json.dumps({"status": result["status"], "remote_requests": result["remote_requests"],
                      "summary": result["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
