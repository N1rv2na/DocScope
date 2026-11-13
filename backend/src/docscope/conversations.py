"""Durable conversation catalog and display history; graph state lives in Checkpointer."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "conversations.sqlite3"
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    thread_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'deleting'))
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES conversations(thread_id),
                    run_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT,
                    status TEXT,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_by_thread
                    ON messages(thread_id, created_at);
            """)

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    def create(self) -> dict:
        thread_id, now = str(uuid.uuid4()), _now()
        with self._connect() as db:
            db.execute("INSERT INTO conversations VALUES (?, ?, ?, 'active')", (thread_id, now, now))
        return {"thread_id": thread_id, "created_at": now, "updated_at": now}

    def list(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT thread_id, created_at, updated_at FROM conversations "
                              "WHERE status = 'active' ORDER BY updated_at DESC, thread_id").fetchall()
        return [dict(row) for row in rows]

    def get(self, thread_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT thread_id, created_at, updated_at FROM conversations "
                             "WHERE thread_id = ? AND status = 'active'", (thread_id,)).fetchone()
            if row is None:
                return None
            messages = db.execute("SELECT message_id, run_id, role, content, status, "
                                  "citations_json, created_at FROM messages WHERE thread_id = ? "
                                  "ORDER BY rowid", (thread_id,)).fetchall()
        return {**dict(row), "messages": [{**{key: value for key, value in dict(item).items()
                                               if key != "citations_json"},
                                           "citations": json.loads(item["citations_json"])}
                                          for item in messages]}

    def append_turn(self, thread_id: str, question: str, result: dict) -> None:
        now = _now()
        with self._connect() as db:
            row = db.execute("SELECT status FROM conversations WHERE thread_id = ?", (thread_id,)).fetchone()
            if row is None or row["status"] != "active":
                raise LookupError("conversation not found")
            db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                (str(uuid.uuid4()), thread_id, result["run_id"], "user", question,
                 None, "[]", now),
                (str(uuid.uuid4()), thread_id, result["run_id"], "assistant",
                 result["answer"], result["status"],
                 json.dumps(result["citations"], ensure_ascii=False), now),
            ])
            db.execute("UPDATE conversations SET updated_at = ? WHERE thread_id = ?", (now, thread_id))

    def mark_deleting(self, thread_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT thread_id FROM conversations WHERE thread_id = ?", (thread_id,)).fetchone()
            if row is None:
                return False
            db.execute("UPDATE conversations SET status = 'deleting' WHERE thread_id = ?", (thread_id,))
        return True

    def pending_deletions(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT thread_id FROM conversations WHERE status = 'deleting'").fetchall()
        return [row["thread_id"] for row in rows]

    def finish_delete(self, thread_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            db.execute("DELETE FROM conversations WHERE thread_id = ? AND status = 'deleting'", (thread_id,))
