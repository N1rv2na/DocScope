"""Bounded completed-turn context and validated standalone retrieval intent."""

from __future__ import annotations

import json


MAX_RECENT_TURNS = 4
MAX_WINDOW_BYTES = 6000
MAX_ANSWER_CHARS = 1000
MAX_QUESTION_CHARS = 500
SUMMARY_TRIGGER_ESTIMATED_TOKENS = 200
MAX_SUMMARY_INPUT_BYTES = 6000
MAX_SUMMARY_BYTES = 1200


def completed_turns(messages: list[dict]) -> list[dict[str, str]]:
    """Project durable messages into completed question/answer pairs only."""
    turns = []
    for user, assistant in zip(messages[::2], messages[1::2]):
        if user.get("role") == "user" and assistant.get("role") == "assistant":
            turns.append({"question": user["content"],
                          "answer": (assistant.get("content") or "")[:MAX_ANSWER_CHARS]})
    return turns


def recent_turns(messages: list[dict]) -> list[dict[str, str | None]]:
    """Keep only recent completed user/assistant pairs; omit citations and run state."""
    window = completed_turns(messages)[-MAX_RECENT_TURNS:]
    while window and len(json.dumps(window, ensure_ascii=False).encode("utf-8")) > MAX_WINDOW_BYTES:
        window.pop(0)
    return window


def summary_batch(messages: list[dict], summarized_turns: int,
                  prior_summary: str | None) -> list[dict[str, str]] | None:
    """Take a bounded prefix older than the live window, once it crosses the frozen threshold.

    This counts estimated tokens as ceil(UTF-8 bytes / 3). The model tokenizer is
    gateway-dependent; the independent byte cap is the hard input bound.
    Only user questions enter the summary model; historical answers are not
    copied into the persisted topic summary through this path.
    """
    older = completed_turns(messages)[:-MAX_RECENT_TURNS]
    unsummarized = [{"question": item["question"]} for item in older[summarized_turns:]]
    if not unsummarized:
        return None
    question_bytes = sum(len(item["question"].encode("utf-8")) for item in unsummarized)
    if (question_bytes + 2) // 3 < SUMMARY_TRIGGER_ESTIMATED_TOKENS:
        return None
    batch = []
    for item in unsummarized:
        proposed = batch + [item]
        request = {"prior_summary": prior_summary, "older_questions": proposed}
        if len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > MAX_SUMMARY_INPUT_BYTES:
            break
        batch = proposed
    return batch or None


def validated_summary(raw: dict) -> str:
    """Accept only a short, nonempty summary; never accept an answer payload."""
    if not isinstance(raw, dict) or set(raw) != {"memory_summary"}:
        raise ValueError("summary must contain only memory_summary")
    value = raw["memory_summary"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("summary must be nonblank text")
    value = value.strip()
    if len(value.encode("utf-8")) > MAX_SUMMARY_BYTES or any(ord(char) < 32 for char in value):
        raise ValueError("summary exceeds byte limit or contains control characters")
    return value


def standalone_question(raw: dict) -> str:
    """Reject malformed rewrites before they can reach retrieval or answer generation."""
    if not isinstance(raw, dict) or set(raw) != {"standalone_question"}:
        raise ValueError("rewrite must contain only standalone_question")
    value = raw["standalone_question"]
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_QUESTION_CHARS:
        raise ValueError("standalone question must be nonblank and at most 500 characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError("standalone question contains control characters")
    return value.strip()
