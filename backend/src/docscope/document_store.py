"""Minimal persistent document import status for the Markdown slice."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class DocumentStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "documents").mkdir(exist_ok=True)
        self.path = root / "documents.sqlite3"
        with self._connect() as database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, document_id: str, filename: str, source_path: str, content_hash: str) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, 'processing', NULL)",
                (document_id, filename, source_path, content_hash),
            )

    def mark_ready(self, document_id: str) -> None:
        with self._connect() as database:
            database.execute("UPDATE documents SET status = 'ready', error_code = NULL WHERE document_id = ?", (document_id,))

    def mark_failed(self, document_id: str, error_code: str) -> None:
        with self._connect() as database:
            database.execute("UPDATE documents SET status = 'failed', error_code = ? WHERE document_id = ?", (error_code, document_id))

    def get(self, document_id: str) -> dict | None:
        with self._connect() as database:
            row = database.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        return dict(row) if row else None

    def ready_ids(self) -> list[str]:
        with self._connect() as database:
            rows = database.execute("SELECT document_id FROM documents WHERE status = 'ready'").fetchall()
        return [row[0] for row in rows]
