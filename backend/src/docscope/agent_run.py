"""One bounded LangGraph retrieval run with a local, inspectable outcome record."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from docscope.llm import ContextLimitError


logger = logging.getLogger(__name__)


class RunState(TypedDict, total=False):
    run_id: str
    question: str
    query: str
    evidence: list[dict[str, Any]]
    retrieved_evidence_ids: list[str]
    trace: list[dict[str, Any]]
    calls: dict[str, int]
    status: str
    stop_reason: str
    error_code: str
    answer: str | None
    citations: list[dict[str, Any]]


class RunStore:
    """Save one final Run per server-generated ID; no conversation state is kept."""

    def __init__(self, root: Path):
        self.directory = root / "runs"

    def save(self, record: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{record['run_id']}.json"
        temporary = self.directory / f".{record['run_id']}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class AgentRunner:
    """Keep graph decisions separate from the injected retrieval and answer services."""

    def __init__(self, retrieve: Callable[[str], list[dict]],
                 generate: Callable[[str, list[dict]], dict],
                 validate: Callable[[dict, list[dict]], dict],
                 sources_current: Callable[[list[dict]], bool]):
        def plan(state: RunState) -> dict:
            started = time.monotonic()
            query = state["question"]
            return {"query": query, "trace": state["trace"] + [{
                "stage": "plan", "status": "completed", "query": query,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            }]}

        def retrieve_once(state: RunState) -> dict:
            started = time.monotonic()
            calls = {**state["calls"], "retrieval": state["calls"]["retrieval"] + 1}
            try:
                evidence = retrieve(state["query"])
                ids = [item["chunk_id"] for item in evidence]
            except Exception:
                logger.exception("Agent retrieval failed for run %s", state["run_id"])
                return {**self._failure("retrieval_failed"), "calls": calls,
                        "trace": state["trace"] + [self._step("retrieve", "failed", started)]}
            update = {"evidence": evidence, "retrieved_evidence_ids": ids, "calls": calls,
                      "trace": state["trace"] + [self._step("retrieve", "completed", started,
                                                           evidence_ids=ids)]}
            if not evidence:
                update.update(status="insufficient_evidence", stop_reason="no_evidence",
                              answer=None, citations=[])
            return update

        def next_stage(state: RunState) -> str:
            return END if state.get("status") else "answer"

        def answer_once(state: RunState) -> dict:
            started = time.monotonic()
            calls = {**state["calls"], "model": state["calls"]["model"] + 1}
            try:
                raw = generate(state["question"], state["evidence"])
            except ContextLimitError:
                return self._answer_failure(state, calls, "context_limit_exceeded", started)
            except ValueError:
                logger.exception("Invalid model output for run %s", state["run_id"])
                return self._answer_failure(state, calls, "model_output_invalid", started)
            except Exception:
                logger.exception("Agent model call failed for run %s", state["run_id"])
                return self._answer_failure(state, calls, "model_failed", started)
            try:
                if not sources_current(state["evidence"]):
                    return self._answer_failure(state, calls, "source_changed", started)
                outcome = validate(raw, state["evidence"])
            except (ValueError, KeyError):
                logger.warning("Rejected model answer for run %s", state["run_id"], exc_info=True)
                return self._answer_failure(state, calls, "model_output_invalid", started)
            except Exception:
                logger.exception("Agent source check failed for run %s", state["run_id"])
                return self._answer_failure(state, calls, "source_check_failed", started)
            stop_reason = "answered" if outcome["status"] == "answered" else "model_abstained"
            return {**outcome, "stop_reason": stop_reason, "calls": calls,
                    "trace": state["trace"] + [self._step("answer", "completed", started)]}

        graph = StateGraph(RunState)
        graph.add_node("plan", plan)
        graph.add_node("retrieve", retrieve_once)
        graph.add_node("answer", answer_once)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "retrieve")
        graph.add_conditional_edges("retrieve", next_stage, ["answer", END])
        graph.add_edge("answer", END)
        self.graph = graph.compile()

    @staticmethod
    def _step(stage: str, status: str, started: float, **details: Any) -> dict:
        return {"stage": stage, "status": status,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3), **details}

    @staticmethod
    def _failure(code: str) -> dict:
        return {"status": "error", "error_code": code, "stop_reason": code,
                "answer": None, "citations": []}

    def _answer_failure(self, state: RunState, calls: dict[str, int],
                        code: str, started: float) -> dict:
        return {**self._failure(code), "calls": calls,
                "trace": state["trace"] + [self._step("answer", "failed", started)]}

    def run(self, question: str) -> dict[str, Any]:
        started = time.monotonic()
        initial: RunState = {"run_id": str(uuid.uuid4()), "question": question,
                             "trace": [], "calls": {"retrieval": 0, "model": 0},
                             "retrieved_evidence_ids": []}
        try:
            state = self.graph.invoke(initial)
        except Exception:
            logger.exception("LangGraph execution failed for run %s", initial["run_id"])
            state = {**initial, **self._failure("graph_failed"),
                     "trace": [self._step("graph", "failed", started)]}
        return {"run_id": state["run_id"], "question": state["question"],
                "query": state.get("query", question),
                "status": state["status"], "stop_reason": state["stop_reason"],
                "answer": state["answer"], "citations": state["citations"],
                "retrieved_evidence_ids": state["retrieved_evidence_ids"],
                "calls": state["calls"], "trace": state["trace"],
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                **({"error_code": state["error_code"]} if state.get("error_code") else {})}
