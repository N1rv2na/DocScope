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
from pydantic import ValidationError

from docscope.llm import ContextLimitError, LLMCallError, LLMConfigurationError


logger = logging.getLogger(__name__)
MAX_RETRIEVAL_ROUNDS = 2
MAX_MODEL_ATTEMPTS = 2
MAX_EXTRA_MODEL_REQUESTS = 1
RETRYABLE_MODEL_ERRORS = {"request_timeout", "request_rate_limited", "request_server_error",
                          "output_truncated", "json_invalid", "schema_invalid"}


class SourceChangedError(Exception):
    """The evidence changed after retrieval and cannot be reused."""


class ProposalSchemaError(ValueError):
    """A Planner response has a malformed object shape or field type."""
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
    retry_requests: int
    model_request_budget: int
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
                 plan_next: Callable[[str, list[dict]], dict] | None = None,
                 extra_model_requests: int = MAX_EXTRA_MODEL_REQUESTS):
        if type(extra_model_requests) is not int or not 0 <= extra_model_requests <= 1:
            raise ValueError("extra_model_requests must be 0 or 1")
        self.extra_model_requests = extra_model_requests
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
            result = self._invoke_model(
                state, "plan_next", lambda: plan_next(state["question"], state["evidence"]),
                lambda proposal: self._validate_proposal(
                    proposal, state["question"], state["query"], state["evidence"]),
                "proposal_invalid", lambda: sources_current(state["evidence"]))
            calls = result["calls"]
            details = result["details"]
            if result["code"]:
                code = self._public_model_error("plan_next", result["code"])
                return {**self._failure(code), "calls": calls,
                        "retry_requests": result["retry_requests"],
                        "trace": state["trace"] + [self._step("plan_next", "failed", started, **details)]}
            assessment, missing_fact, next_query, bridge_text, source_chunk_id, stop_reason = result["value"]
            if next_query is None:
                update = {"calls": calls, "retry_requests": result["retry_requests"],
                          "followup_ready": False,
                          "trace": state["trace"] + [self._step("plan_next", "completed", started,
                                                               evidence_status=assessment,
                                                               missing_fact=missing_fact, reason=stop_reason,
                                                               **details)]}
                if assessment == "missing":
                    update.update(status="insufficient_evidence", stop_reason=stop_reason,
                                  answer=None, citations=[])
                return update
            return {"query": next_query, "calls": calls,
                    "retry_requests": result["retry_requests"], "followup_ready": True,
                    "trace": state["trace"] + [self._step("plan_next", "completed", started,
                                                         evidence_status=assessment,
                                                         missing_fact=missing_fact,
                                                         next_query=next_query, bridge_text=bridge_text,
                                                         source_chunk_id=source_chunk_id, **details)]}

        def after_followup(state: RunState) -> str:
            if state.get("status"):
                return END
            return "retrieve" if state.get("followup_ready") else "answer"

        def answer_once(state: RunState) -> dict:
            started = time.monotonic()
            def validate_answer(raw: dict) -> dict:
                if not sources_current(state["evidence"]):
                    raise SourceChangedError()
                return validate(raw, state["evidence"])

            result = self._invoke_model(state, "answer",
                                        lambda: generate(state["question"], state["evidence"]),
                                        validate_answer, "answer_invalid",
                                        lambda: sources_current(state["evidence"]))
            calls = result["calls"]
            details = result["details"]
            if result["code"]:
                return {**self._answer_failure(state, calls,
                                               self._public_model_error("answer", result["code"]),
                                               started, **details),
                        "retry_requests": result["retry_requests"]}
            outcome = result["value"]
            if outcome["status"] == "answered":
                stop_reason = "answered"
            elif plan_next is not None and state["retrieval_round"] >= MAX_RETRIEVAL_ROUNDS:
                stop_reason = "retrieval_budget_exhausted"
            else:
                stop_reason = "model_abstained"
            return {**outcome, "stop_reason": stop_reason, "calls": calls,
                    "retry_requests": result["retry_requests"],
                    "trace": state["trace"] + [self._step("answer", "completed", started, **details)]}

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
            raise ProposalSchemaError("evidence assessment must contain exactly five fields")
        assessment = proposal["evidence_status"]
        missing_fact = proposal["missing_fact"]
        query, bridge, source_id = (proposal[key] for key in ("next_query", "bridge_text", "source_chunk_id"))
        if assessment == "sufficient":
            if any(value is not None for value in (missing_fact, query, bridge, source_id)):
                raise ValueError("sufficient assessment cannot request another retrieval")
            return assessment, None, None, None, None, "sufficient"
        if assessment != "missing":
            raise ValueError("evidence assessment status is inconsistent")
        if not isinstance(missing_fact, str):
            raise ProposalSchemaError("missing fact must be text")
        if not missing_fact.strip() or len(missing_fact) > MAX_MISSING_FACT_CHARS:
            raise ValueError("missing assessment requires a bounded missing fact")
        missing_fact = missing_fact.strip()
        if query is None:
            if bridge is not None or source_id is not None:
                raise ValueError("a missing query cannot cite a bridge")
            return assessment, missing_fact, None, None, None, "missing_fact_unresolved"
        if not isinstance(query, str) or not isinstance(bridge, str) or not isinstance(source_id, str):
            raise ProposalSchemaError("next-query fields have invalid types")
        if (not query.strip() or len(query) > MAX_FOLLOWUP_QUERY_CHARS
                or not bridge.strip() or len(bridge) > MAX_BRIDGE_TEXT_CHARS):
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

    def _invoke_model(self, state: RunState, stage: str, request: Callable[[], dict],
                      check: Callable[[dict], Any], invalid_code: str,
                      can_retry_source: Callable[[], bool]) -> dict:
        """Run one logical stage with at most one extra request across the whole Run."""
        calls = dict(state["calls"])
        retries = state["retry_requests"]
        budget = state["model_request_budget"]
        attempts = []
        value = None
        code = None
        previous_error = None
        last_diagnostic = None
        for local_attempt in range(2):
            if calls["model"] >= budget:
                code = "model_budget_exhausted"
                break
            if local_attempt:
                try:
                    if not can_retry_source():
                        code = "source_changed"
                        break
                except Exception:
                    code = "source_check_failed"
                    break
            attempted = True
            diagnostic = None
            try:
                raw = request()
            except ContextLimitError:
                code, attempted = "context_limit_exceeded", False
            except LLMConfigurationError:
                code, attempted = "configuration_invalid", False
            except LLMCallError as exc:
                code, diagnostic = exc.code, exc.diagnostic
            except TimeoutError:
                code = "request_timeout"
            except ValueError:
                code = "output_invalid"
            except Exception:
                code = "request_failed"
            else:
                diagnostic = getattr(raw, "diagnostic", None)
                try:
                    value = check(raw)
                except SourceChangedError:
                    code = "source_changed"
                except ValidationError:
                    code = "schema_invalid"
                    logger.warning("Rejected %s model schema for run %s", stage, state["run_id"])
                except ProposalSchemaError:
                    code = "schema_invalid"
                    logger.warning("Rejected %s model schema for run %s", stage, state["run_id"])
                except (ValueError, KeyError):
                    code = invalid_code
                    logger.warning("Rejected %s model output for run %s", stage, state["run_id"])
                except Exception:
                    code = "source_check_failed" if stage == "answer" else "request_failed"
                else:
                    code = None
            if not attempted:
                break
            calls["model"] += 1
            if local_attempt:
                retries += 1
            attempts.append({"attempt": local_attempt + 1, "retry_reason": previous_error,
                             "error_code": code, "llm_call": diagnostic})
            last_diagnostic = diagnostic
            if code is None:
                break
            remaining = budget - calls["model"]
            reserve_answer = 1 if stage == "plan_next" else 0
            if (code in RETRYABLE_MODEL_ERRORS and local_attempt == 0
                    and retries < self.extra_model_requests and remaining > reserve_answer):
                previous_error = code
                continue
            break
        details = {"llm_attempts": attempts}
        if last_diagnostic is not None:
            details["llm_call"] = last_diagnostic
        if code is not None:
            details["llm_error_code"] = code
            if code in RETRYABLE_MODEL_ERRORS:
                details["recovery_stop_reason"] = ("request_budget_exhausted" if calls["model"] >= budget
                                                   else "retry_limit_reached")
        return {"value": value, "code": code, "calls": calls,
                "retry_requests": retries, "details": details}

    @staticmethod
    def _public_model_error(stage: str, code: str) -> str:
        if code in {"context_limit_exceeded", "source_changed", "model_budget_exhausted",
                    "source_check_failed"}:
            return code
        if code in {"output_truncated", "json_invalid", "schema_invalid", "response_incomplete",
                    "output_invalid", "answer_invalid", "proposal_invalid"}:
            return "plan_output_invalid" if stage == "plan_next" else "model_output_invalid"
        return "planner_failed" if stage == "plan_next" else "model_failed"

    @staticmethod
    def _step(stage: str, status: str, started: float, **details: Any) -> dict:
        return {"stage": stage, "status": status,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3), **details}

    @staticmethod
    def _failure(code: str) -> dict:
        return {"status": "error", "error_code": code, "stop_reason": code,
                "answer": None, "citations": []}

    def _answer_failure(self, state: RunState, calls: dict[str, int],
                        code: str, started: float, **details: Any) -> dict:
        return {**self._failure(code), "calls": calls,
                "trace": state["trace"] + [self._step("answer", "failed", started, **details)]}

    def run(self, question: str) -> dict[str, Any]:
        started = time.monotonic()
        initial = self._initial_state(question)
        try:
            state = self.graph.invoke(initial)
        except Exception:
            logger.exception("LangGraph execution failed for run %s", initial["run_id"])
            state = {**initial, **self._failure("graph_failed"),
                     "trace": [self._step("graph", "failed", started)]}
        return self._record(state, started)

    def stream_run(self, question: str, run_id: str):
        """Yield completed graph stages, then one final Run record."""
        started = time.monotonic()
        initial = self._initial_state(question, run_id)
        state: RunState = initial.copy()
        try:
            for update in self.graph.stream(initial, stream_mode="updates"):
                for stage, fields in update.items():
                    if stage == "__interrupt__":
                        continue
                    state.update(fields)
                    step = fields["trace"][-1]
                    yield "stage", {key: step[key] for key in (
                        "stage", "status", "elapsed_ms", "round", "query", "evidence_ids",
                        "new_evidence_ids", "next_query", "source_chunk_id", "reason",
                        "evidence_status") if key in step}
        except Exception:
            logger.exception("LangGraph streaming failed for run %s", run_id)
            state = {**state, **self._failure("graph_failed"),
                     "trace": state["trace"] + [self._step("graph", "failed", started)]}
            yield "stage", {"stage": "graph", "status": "failed"}
        yield "result", self._record(state, started)

    def _initial_state(self, question: str, run_id: str | None = None) -> RunState:
        return {"run_id": run_id or str(uuid.uuid4()), "question": question,
                "trace": [], "calls": {"retrieval": 0, "model": 0},
                "retry_requests": 0,
                "model_request_budget": MAX_MODEL_ATTEMPTS + self.extra_model_requests,
                "retrieved_evidence_ids": [], "retrieval_round": 0,
                "followup_ready": False}

    @staticmethod
    def _record(state: RunState, started: float) -> dict[str, Any]:
        attempts = [attempt for step in state["trace"] for attempt in step.get("llm_attempts", [])]
        usage = {}
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
            values = [(attempt.get("llm_call") or {}).get(key) for attempt in attempts]
            usage[f"known_{key}"] = sum(value for value in values if type(value) is int and value >= 0)
            usage[f"unknown_{key}_calls"] = sum(value is None for value in values)
        return {"run_id": state["run_id"], "question": state["question"],
                "query": state.get("query", state["question"]),
                "status": state["status"], "stop_reason": state["stop_reason"],
                "answer": state["answer"], "citations": state["citations"],
                "retrieved_evidence_ids": state["retrieved_evidence_ids"],
                "calls": state["calls"], "trace": state["trace"],
                "model_request_budget": state["model_request_budget"],
                "retry_requests": state["retry_requests"], "model_usage": usage,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                **({"error_code": state["error_code"]} if state.get("error_code") else {})}
