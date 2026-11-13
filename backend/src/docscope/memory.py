"""Bounded completed-turn context and validated standalone retrieval intent."""

from __future__ import annotations

import json


MAX_RECENT_TURNS = 4
MAX_WINDOW_BYTES = 6000
MAX_ANSWER_CHARS = 1000
MAX_QUESTION_CHARS = 500


def recent_turns(messages: list[dict]) -> list[dict[str, str | None]]:
    """Keep only recent completed user/assistant pairs; omit citations and run state."""
    turns = []
    for user, assistant in zip(messages[::2], messages[1::2]):
        if user.get("role") != "user" or assistant.get("role") != "assistant":
            continue
        turns.append({"question": user["content"],
                      "answer": (assistant.get("content") or "")[:MAX_ANSWER_CHARS]})
    window = turns[-MAX_RECENT_TURNS:]
    while window and len(json.dumps(window, ensure_ascii=False).encode("utf-8")) > MAX_WINDOW_BYTES:
        window.pop(0)
    return window


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
