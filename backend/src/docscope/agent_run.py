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
MAX_RETRIEVAL_ROUNDS = 2
MAX_MODEL_ATTEMPTS = 2
MAX_FOLLOWUP_QUERY_CHARS = 500
MAX_BRIDGE_TEXT_CHARS = 120
MAX_MISSING_FACT_CHARS = 240


class RunState(TypedDict, total=False):
    run_id: str
    question: str
    query: str
    evidence: list[dict[str, Any]]
    retrieved_evidence_ids: list[str]
    retrieval_round: int
    followup_ready: bool
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
                 sources_current: Callable[[list[dict]], bool],
                 plan_next: Callable[[str, list[dict]], dict] | None = None):
        def plan(state: RunState) -> dict:
            started = time.monotonic()
            query = state["question"]
            return {"query": query, "trace": state["trace"] + [{
                "stage": "plan", "status": "completed", "query": query,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            }]}

        def retrieve_once(state: RunState) -> dict:
            started = time.monotonic()
            if state["retrieval_round"] >= MAX_RETRIEVAL_ROUNDS:
                return {**self._failure("retrieval_budget_exhausted"),
                        "trace": state["trace"] + [self._step("retrieve", "failed", started)]}
            calls = {**state["calls"], "retrieval": state["calls"]["retrieval"] + 1}
            try:
                evidence = retrieve(state["query"])
                ids = [item["chunk_id"] for item in evidence]
            except Exception:
                logger.exception("Agent retrieval failed for run %s", state["run_id"])
                return {**self._failure("retrieval_failed"), "calls": calls,
                        "trace": state["trace"] + [self._step("retrieve", "failed", started)]}
            existing_ids = set(state["retrieved_evidence_ids"])
            new_evidence = []
            for item in evidence:
                if item["chunk_id"] not in existing_ids:
                    new_evidence.append(item)
                    existing_ids.add(item["chunk_id"])
            combined = state.get("evidence", []) + new_evidence
            round_number = state["retrieval_round"] + 1
            update = {"evidence": combined,
                      "retrieved_evidence_ids": state["retrieved_evidence_ids"] + [
                          item["chunk_id"] for item in new_evidence],
                      "retrieval_round": round_number, "calls": calls,
                      "trace": state["trace"] + [self._step("retrieve", "completed", started,
                                                           round=round_number, query=state["query"],
                                                           evidence_ids=ids,
                                                           new_evidence_ids=[item["chunk_id"] for item in new_evidence])]}
            if not combined:
                update.update(status="insufficient_evidence", stop_reason="no_evidence",
                              answer=None, citations=[])
            elif round_number > 1 and not new_evidence:
                update.update(status="insufficient_evidence", stop_reason="no_new_evidence",
                              answer=None, citations=[])
            return update

        def next_stage(state: RunState) -> str:
            if state.get("status"):
                return END
            if plan_next is not None and state["retrieval_round"] < MAX_RETRIEVAL_ROUNDS:
                return "plan_next"
            return "answer"

        def plan_from_evidence(state: RunState) -> dict:
            started = time.monotonic()
            if state["calls"]["model"] >= MAX_MODEL_ATTEMPTS:
                return {**self._failure("model_budget_exhausted"),
                        "trace": state["trace"] + [self._step("plan_next", "failed", started)]}
            calls = {**state["calls"], "model": state["calls"]["model"] + 1}
            try:
                proposal = plan_next(state["question"], state["evidence"])
            except ContextLimitError:
                code = "context_limit_exceeded"
            except ValueError:
                logger.exception("Invalid next-query output for run %s", state["run_id"])
                code = "plan_output_invalid"
            except Exception:
                logger.exception("Next-query planning failed for run %s", state["run_id"])
                code = "planner_failed"
            else:
                code = None
            if code:
                return {**self._failure(code), "calls": calls,
                        "trace": state["trace"] + [self._step("plan_next", "failed", started)]}
            try:
                assessment, missing_fact, next_query, bridge_text, source_chunk_id, stop_reason = self._validate_proposal(
                    proposal, state["question"], state["query"], state["evidence"])
            except ValueError:
                logger.warning("Rejected next-query proposal for run %s", state["run_id"], exc_info=True)
                return {**self._failure("plan_output_invalid"), "calls": calls,
                        "trace": state["trace"] + [self._step("plan_next", "failed", started)]}
            if next_query is None:
                update = {"calls": calls, "followup_ready": False,
                          "trace": state["trace"] + [self._step("plan_next", "completed", started,
                                                               evidence_status=assessment,
                                                               missing_fact=missing_fact, reason=stop_reason)]}
                if assessment == "missing":
                    update.update(status="insufficient_evidence", stop_reason=stop_reason,
                                  answer=None, citations=[])
                return update
            return {"query": next_query, "calls": calls, "followup_ready": True,
                    "trace": state["trace"] + [self._step("plan_next", "completed", started,
                                                         evidence_status=assessment,
                                                         missing_fact=missing_fact,
                                                         next_query=next_query, bridge_text=bridge_text,
                                                         source_chunk_id=source_chunk_id)]}

        def after_followup(state: RunState) -> str:
            if state.get("status"):
                return END
            return "retrieve" if state.get("followup_ready") else "answer"

        def answer_once(state: RunState) -> dict:
            started = time.monotonic()
            if state["calls"]["model"] >= MAX_MODEL_ATTEMPTS:
                return self._answer_failure(state, state["calls"], "model_budget_exhausted", started)
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
            if outcome["status"] == "answered":
                stop_reason = "answered"
            elif plan_next is not None and state["retrieval_round"] >= MAX_RETRIEVAL_ROUNDS:
                stop_reason = "retrieval_budget_exhausted"
            else:
                stop_reason = "model_abstained"
            return {**outcome, "stop_reason": stop_reason, "calls": calls,
                    "trace": state["trace"] + [self._step("answer", "completed", started)]}

        graph = StateGraph(RunState)
        graph.add_node("plan", plan)
        graph.add_node("retrieve", retrieve_once)
        graph.add_node("plan_next", plan_from_evidence)
        graph.add_node("answer", answer_once)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "retrieve")
        graph.add_conditional_edges("retrieve", next_stage, ["plan_next", "answer", END])
        graph.add_conditional_edges("plan_next", after_followup, ["retrieve", "answer", END])
        graph.add_edge("answer", END)
        self.graph = graph.compile()

    @staticmethod
    def _validate_proposal(proposal: dict | None, question: str, prior_query: str,
                           evidence: list[dict]) -> tuple[str, str | None, str | None,
                                                         str | None, str | None, str | None]:
        required = {"evidence_status", "missing_fact", "next_query", "bridge_text", "source_chunk_id"}
        if not isinstance(proposal, dict) or set(proposal) != required:
            raise ValueError("evidence assessment must contain exactly five fields")
        assessment = proposal["evidence_status"]
        missing_fact = proposal["missing_fact"]
        query, bridge, source_id = (proposal[key] for key in ("next_query", "bridge_text", "source_chunk_id"))
        if assessment == "sufficient":
            if any(value is not None for value in (missing_fact, query, bridge, source_id)):
                raise ValueError("sufficient assessment cannot request another retrieval")
            return assessment, None, None, None, None, "sufficient"
        if assessment != "missing" or not isinstance(missing_fact, str) or not missing_fact.strip() or len(missing_fact) > MAX_MISSING_FACT_CHARS:
            raise ValueError("missing assessment requires a bounded missing fact")
        missing_fact = missing_fact.strip()
        if query is None:
            if bridge is not None or source_id is not None:
                raise ValueError("a missing query cannot cite a bridge")
            return assessment, missing_fact, None, None, None, "missing_fact_unresolved"
        if (not isinstance(query, str) or not query.strip() or len(query) > MAX_FOLLOWUP_QUERY_CHARS
                or not isinstance(bridge, str) or not bridge.strip() or len(bridge) > MAX_BRIDGE_TEXT_CHARS
                or not isinstance(source_id, str)):
            raise ValueError("invalid next-query fields")
        source = next((item for item in evidence if item["chunk_id"] == source_id), None)
        normalized = lambda value: " ".join(value.casefold().split())
        if source is None or normalized(bridge) not in normalized(source["chunk_text"]):
            raise ValueError("bridge text is not in the cited retrieved Chunk")
        if normalized(bridge) in normalized(question):
            raise ValueError("bridge text was already present in the initial question")
        if normalized(query) == normalized(prior_query):
            return assessment, missing_fact, None, bridge, source_id, "repeated_query"
        if normalized(bridge) not in normalized(query):
            raise ValueError("next query does not use the discovered bridge")
        return assessment, missing_fact, query.strip(), bridge.strip(), source_id, None

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
                             "retrieved_evidence_ids": [], "retrieval_round": 0,
                             "followup_ready": False}
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
