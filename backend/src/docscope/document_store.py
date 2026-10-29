"""SQLite document identity and lifecycle state; searchable text lives in Qdrant."""

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
            columns = {row["name"] for row in database.execute("PRAGMA table_info(documents)")}
            if "index_version" not in columns:
                database.execute("ALTER TABLE documents ADD COLUMN index_version INTEGER NOT NULL DEFAULT 1")
            if "chunk_count" not in columns:
                database.execute("ALTER TABLE documents ADD COLUMN chunk_count INTEGER")
            # One backend process owns this store. A persisted in-progress state
            # at process startup means the preceding operation was interrupted.
            database.execute("UPDATE documents SET status = 'failed', error_code = 'interrupted' WHERE status = 'processing'")
            database.execute("UPDATE documents SET status = 'failed', error_code = 'delete_failed' WHERE status = 'deleting'")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, document_id: str, filename: str, source_path: str, content_hash: str) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO documents (document_id, filename, source_path, content_hash, status, error_code) VALUES (?, ?, ?, ?, 'processing', NULL)",
                (document_id, filename, source_path, content_hash),
            )

    def mark_ready(self, document_id: str, chunk_count: int) -> None:
        with self._connect() as database:
            database.execute("UPDATE documents SET status = 'ready', error_code = NULL, chunk_count = ? WHERE document_id = ?", (chunk_count, document_id))

    def list_documents(self) -> list[dict]:
        with self._connect() as database:
            return [dict(row) for row in database.execute("SELECT * FROM documents WHERE status != 'deleted' ORDER BY rowid")]

    def named(self, filename: str) -> list[dict]:
        with self._connect() as database:
            return [dict(row) for row in database.execute("SELECT * FROM documents WHERE filename = ? AND status != 'deleted'", (filename,))]

    def mark_failed(self, document_id: str, error_code: str) -> None:
        with self._connect() as database:
            database.execute("UPDATE documents SET status = 'failed', error_code = ? WHERE document_id = ?", (error_code, document_id))

    def begin_replace(self, document_id: str, filename: str, source_path: str, content_hash: str) -> None:
        with self._connect() as database:
            database.execute("""UPDATE documents SET filename = ?, source_path = ?, content_hash = ?,
                status = 'processing', error_code = NULL, chunk_count = NULL,
                index_version = index_version + 1 WHERE document_id = ?""",
                (filename, source_path, content_hash, document_id))

    def mark_deleting(self, document_id: str) -> None:
        with self._connect() as database:
            database.execute("UPDATE documents SET status = 'deleting', error_code = NULL WHERE document_id = ?", (document_id,))

    def mark_deleted(self, document_id: str) -> None:
        with self._connect() as database:
            database.execute("UPDATE documents SET status = 'deleted', error_code = NULL, chunk_count = 0 WHERE document_id = ?", (document_id,))

    def get(self, document_id: str) -> dict | None:
        with self._connect() as database:
            row = database.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        return dict(row) if row else None

    def ready_ids(self) -> list[str]:
        with self._connect() as database:
            rows = database.execute("SELECT document_id FROM documents WHERE status = 'ready'").fetchall()
        return [row[0] for row in rows]
