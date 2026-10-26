"""M1 Markdown upload and Dense Evidence API; no answer generation yet."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from docscope.document_store import DocumentStore
from docscope.embedding import LocalEmbedder
from docscope.markdown import markdown_chunks
from docscope.qdrant_index import QdrantIndex


logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 1024 * 1024


class DenseQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query cannot be blank")
        return value.strip()


def _validate_upload(file: UploadFile) -> tuple[str, bytes, str]:
    filename = file.filename or ""
    disposition = file.headers.get("content-disposition", "")
    # Multipart parsers can reduce Windows paths to a basename. Check the raw
    # part header as well so a supplied path is rejected instead of accepted.
    if any(char in disposition for char in ("/", "\\", ":")):
        raise HTTPException(400, "unsafe filename")
    if not filename or filename in {".", ".."} or any(char in filename for char in ("/", "\\", ":")):
        raise HTTPException(400, "unsafe filename")
    if any(ord(char) < 32 for char in filename):
        raise HTTPException(400, "unsafe filename")
    if Path(filename).suffix.lower() not in {".md", ".markdown"}:
        raise HTTPException(415, "only Markdown files are supported")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Markdown file exceeds 1 MiB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(400, "Markdown must be UTF-8 text") from error
    if not text.strip() or "\x00" in text:
        raise HTTPException(400, "Markdown contains no usable text")
    return filename, content, text


def create_app(data_dir: Path | None = None, embedder: Any = None, index: Any = None) -> FastAPI:
    root = (data_dir if data_dir is not None else Path(os.getenv("DOCSCOPE_DATA_DIR", "data"))).resolve()
    embedding = embedder if embedder is not None else LocalEmbedder()
    qdrant = index if index is not None else QdrantIndex(os.getenv("QDRANT_URL", "http://127.0.0.1:6333"))
    store: DocumentStore | None = None
    api = FastAPI(title="DocScope", version="0.1.0")

    def documents() -> DocumentStore:
        nonlocal store
        if store is None:
            store = DocumentStore(root)
        return store

    @api.post("/api/documents", status_code=201, response_model=None)
    def upload_document(file: UploadFile = File(...)) -> dict | JSONResponse:
        filename, content, text = _validate_upload(file)
        document_id = str(uuid.uuid4())
        source_path = f"documents/{document_id}.md"
        document_store = documents()
        document_store.create(document_id, filename, source_path, hashlib.sha256(content).hexdigest())
        stage = "storage_failed"
        index_started = False
        try:
            (root / source_path).write_bytes(content)
            stage = "parse_failed"
            chunks = markdown_chunks(text)
            if not chunks:
                raise ValueError("Markdown has no indexable body")
            stage = "embedding_failed"
            vectors = embedding.encode([chunk["chunk_text"] for chunk in chunks])
            if len(vectors) != len(chunks) or any(not vector for vector in vectors):
                raise ValueError("embedding output does not match chunks")
            points = []
            for number, (chunk, vector) in enumerate(zip(chunks, vectors)):
                chunk_id = str(uuid.uuid5(uuid.UUID(document_id), f"{number}:{chunk['chunk_text']}"))
                points.append({
                    "id": chunk_id,
                    "vector": {"dense": vector},
                    "payload": {
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "document_title": filename,
                        "source_type": "markdown",
                        "source_path": source_path,
                        "section": chunk["section"],
                        "line_start": chunk["line_start"],
                        "chunk_text": chunk["chunk_text"],
                        "content_hash": hashlib.sha256(content).hexdigest(),
                        "index_version": 1,
                    },
                })
            stage = "index_failed"
            index_started = True
            qdrant.ensure()
            qdrant.upsert(points)
            stage = "state_failed"
            document_store.mark_ready(document_id)
        except Exception:
            logger.exception("Document ingestion failed at %s for %s", stage, document_id)
            document_store.mark_failed(document_id, stage)
            if index_started:
                try:
                    qdrant.delete_document(document_id)
                except Exception:
                    logger.warning("Failed to clean unpublished Qdrant points for %s", document_id, exc_info=True)
            return JSONResponse(status_code=503, content={
                "document_id": document_id,
                "status": "failed",
                "error_code": stage,
            })
        return {
            "document_id": document_id,
            "status": "ready",
            "filename": filename,
            "source_path": source_path,
            "chunk_count": len(chunks),
        }

    @api.get("/api/documents/{document_id}")
    def document_status(document_id: str) -> dict:
        record = documents().get(document_id)
        if record is None:
            raise HTTPException(404, "document not found")
        return record

    @api.post("/api/retrieval/dense")
    def retrieve(request: DenseQuery) -> dict:
        ready_ids = documents().ready_ids()
        if not ready_ids:
            return {"evidence": []}
        try:
            query_vector = embedding.encode([request.query])[0]
            points = qdrant.query(query_vector, ready_ids, request.limit)
        except Exception as error:
            logger.exception("Dense retrieval failed")
            raise HTTPException(503, "dense retrieval failed") from error
        evidence: list[dict[str, Any]] = []
        for point in points:
            payload = point["payload"]
            if payload["document_id"] not in ready_ids:
                continue
            evidence.append({
                "document_id": payload["document_id"],
                "chunk_id": payload["chunk_id"],
                "document_title": payload["document_title"],
                "source_path": payload["source_path"],
                "section": payload["section"],
                "line_start": payload["line_start"],
                "chunk_text": payload["chunk_text"],
                "retrieval_stage": "dense",
                "rank": len(evidence) + 1,
                "score": point["score"],
            })
        return {"evidence": evidence}

    return api


app = create_app()
