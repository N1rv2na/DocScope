"""M1 Markdown/PDF upload, Dense Evidence and single-question answering API."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from docscope.document_store import DocumentStore
from docscope.embedding import LocalEmbedder
from docscope.llm import ContextLimitError, OpenAIResponder
from docscope.markdown import markdown_chunks
from docscope.pdf import pdf_chunks
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


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question cannot be blank")
        return value.strip()


class ModelAnswer(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citation_ids: list[str]


def _validate_upload(file: UploadFile) -> tuple[str, bytes, str | None]:
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
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".markdown", ".pdf"}:
        raise HTTPException(415, "only Markdown and PDF files are supported")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file exceeds 1 MiB")
    if suffix == ".pdf":
        return filename, content, None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(400, "Markdown must be UTF-8 text") from error
    if not text.strip() or "\x00" in text:
        raise HTTPException(400, "Markdown contains no usable text")
    return filename, content, text


def create_app(data_dir: Path | None = None, embedder: Any = None, index: Any = None, llm: Any = None) -> FastAPI:
    root = (data_dir if data_dir is not None else Path(os.getenv("DOCSCOPE_DATA_DIR", "data"))).resolve()
    embedding = embedder if embedder is not None else LocalEmbedder()
    qdrant = index if index is not None else QdrantIndex(os.getenv("QDRANT_URL", "http://127.0.0.1:6333"))
    responder = llm if llm is not None else OpenAIResponder()
    store: DocumentStore | None = None
    lifecycle_lock = RLock()
    api = FastAPI(title="DocScope", version="0.1.0")

    def documents() -> DocumentStore:
        nonlocal store
        if store is None:
            with lifecycle_lock:
                if store is None:
                    store = DocumentStore(root)
        return store

    @api.post("/api/documents", status_code=201, response_model=None)
    def upload_document(file: UploadFile = File(...)) -> dict | JSONResponse:
        with lifecycle_lock:
            return ingest(file)

    @api.put("/api/documents/{document_id}", response_model=None)
    def replace_document(document_id: str, file: UploadFile = File(...)) -> dict | JSONResponse:
        with lifecycle_lock:
            return ingest(file, document_id)

    @api.get("/api/documents")
    def list_documents() -> dict:
        return {"documents": documents().list_documents()}

    def remove_source_files(document_id: str) -> None:
        for suffix in (".md", ".pdf"):
            (root / "documents" / f"{document_id}{suffix}").unlink(missing_ok=True)

    def ingest(file: UploadFile, target_id: str | None = None) -> dict | JSONResponse:
        filename, content, text = _validate_upload(file)
        document_store = documents()
        content_hash = hashlib.sha256(content).hexdigest()
        matches = document_store.named(filename)
        if target_id is not None:
            existing = document_store.get(target_id)
            if existing is None or existing["status"] == "deleted":
                raise HTTPException(404, "document not found")
            if existing["filename"] != filename and any(item["document_id"] != target_id for item in matches):
                raise HTTPException(409, "filename belongs to another document")
        elif len(matches) > 1:
            raise HTTPException(409, "ambiguous filename; select a document ID")
        else:
            existing = matches[0] if matches else None
        if existing and existing["status"] == "ready" and existing["content_hash"] == content_hash and existing["filename"] == filename:
            return JSONResponse(status_code=200, content=existing)
        document_id = existing["document_id"] if existing else str(uuid.uuid4())
        is_pdf = Path(filename).suffix.lower() == ".pdf"
        source_path = f"documents/{document_id}{'.pdf' if is_pdf else '.md'}"
        if existing:
            document_store.begin_replace(document_id, filename, source_path, content_hash)
        else:
            document_store.create(document_id, filename, source_path, content_hash)
        index_version = document_store.get(document_id)["index_version"]
        stage = "storage_failed"
        index_started = False
        try:
            if existing:
                stage = "index_failed"
                qdrant.delete_document(document_id)
                stage = "storage_failed"
                remove_source_files(document_id)
            (root / source_path).write_bytes(content)
            stage = "parse_failed"
            chunks = pdf_chunks(content) if is_pdf else markdown_chunks(text or "")
            if not chunks:
                raise ValueError("document has no extractable text")
            stage = "embedding_failed"
            vectors = embedding.encode([chunk["chunk_text"] for chunk in chunks])
            if len(vectors) != len(chunks) or any(not vector for vector in vectors):
                raise ValueError("embedding output does not match chunks")
            points = []
            for number, (chunk, vector) in enumerate(zip(chunks, vectors)):
                chunk_id = str(uuid.uuid5(uuid.UUID(document_id), f"{index_version}:{number}:{chunk['chunk_text']}"))
                points.append({
                    "id": chunk_id,
                    "vector": {"dense": vector},
                    "payload": {
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "document_title": filename,
                        "source_type": "pdf" if is_pdf else "markdown",
                        "source_path": source_path,
                        "page_number": chunk.get("page_number"),
                        "section": chunk.get("section"),
                        "line_start": chunk.get("line_start"),
                        "chunk_text": chunk["chunk_text"],
                        "content_hash": content_hash,
                        "index_version": index_version,
                    },
                })
            stage = "index_failed"
            index_started = True
            qdrant.ensure()
            qdrant.upsert(points)
            stage = "state_failed"
            document_store.mark_ready(document_id, len(chunks))
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
        return JSONResponse(status_code=200 if existing else 201, content=document_store.get(document_id))

    @api.delete("/api/documents/{document_id}", response_model=None)
    def delete_document(document_id: str) -> dict | JSONResponse:
        with lifecycle_lock:
            document_store = documents()
            record = document_store.get(document_id)
            if record is None:
                raise HTTPException(404, "document not found")
            if record["status"] == "deleted":
                return {"document_id": document_id, "status": "deleted"}
            document_store.mark_deleting(document_id)
            try:
                qdrant.delete_document(document_id)
                remove_source_files(document_id)
                document_store.mark_deleted(document_id)
            except Exception:
                logger.exception("Document deletion failed for %s", document_id)
                document_store.mark_failed(document_id, "delete_failed")
                return JSONResponse(status_code=503, content={"document_id": document_id, "status": "failed", "error_code": "delete_failed"})
            return {"document_id": document_id, "status": "deleted"}

    @api.get("/api/documents/{document_id}")
    def document_status(document_id: str) -> dict:
        record = documents().get(document_id)
        if record is None:
            raise HTTPException(404, "document not found")
        return record

    def current_payload(payload: dict, record: dict | None) -> bool:
        return bool(record and record["status"] == "ready"
                    and payload.get("document_id") == record["document_id"]
                    and payload.get("content_hash") == record["content_hash"]
                    and payload.get("index_version") == record["index_version"])

    @api.get("/api/documents/{document_id}/citations/{chunk_id}", response_model=None)
    def citation_status(document_id: str, chunk_id: str) -> dict | JSONResponse:
        with lifecycle_lock:
            record = documents().get(document_id)
            if not record or record["status"] != "ready":
                return {"status": "invalid"}
            try:
                uuid.UUID(chunk_id)
            except ValueError:
                return {"status": "invalid"}
            try:
                point = qdrant.get_point(chunk_id)
            except Exception:
                logger.exception("Citation lookup failed")
                return JSONResponse(status_code=503, content={"status": "error", "error_code": "citation_check_failed"})
            valid = bool(point and point["payload"].get("chunk_id") == chunk_id and current_payload(point["payload"], record))
            return {"status": "valid" if valid else "invalid"}

    def dense_evidence(request: DenseQuery) -> list[dict[str, Any]]:
        with lifecycle_lock:
            return retrieve_current(request)

    def retrieve_current(request: DenseQuery) -> list[dict[str, Any]]:
        records = {item["document_id"]: item for item in documents().list_documents() if item["status"] == "ready"}
        ready_ids = list(records)
        if not ready_ids:
            return []
        try:
            query_vector = embedding.encode([request.query])[0]
            points = qdrant.query(query_vector, ready_ids, request.limit)
        except Exception as error:
            logger.exception("Dense retrieval failed")
            raise HTTPException(503, "dense retrieval failed") from error
        evidence: list[dict[str, Any]] = []
        for point in points:
            payload = point["payload"]
            if not current_payload(payload, records.get(payload["document_id"])):
                continue
            evidence.append({
                "document_id": payload["document_id"],
                "chunk_id": payload["chunk_id"],
                "document_title": payload["document_title"],
                "source_path": payload["source_path"],
                "index_version": payload["index_version"],
                "page_number": payload.get("page_number"),
                "section": payload["section"],
                "line_start": payload["line_start"],
                "chunk_text": payload["chunk_text"],
                "retrieval_stage": "dense",
                "rank": len(evidence) + 1,
                "score": point["score"],
            })
        return evidence

    @api.post("/api/retrieval/dense")
    def retrieve(request: DenseQuery) -> dict:
        return {"evidence": dense_evidence(request)}

    @api.post("/api/chat", response_model=None)
    def chat(request: ChatQuestion) -> dict | JSONResponse:
        run_id = str(uuid.uuid4())
        try:
            evidence = dense_evidence(DenseQuery(query=request.question, limit=5))
        except Exception:
            logger.exception("Retrieval failed for run %s", run_id)
            return JSONResponse(status_code=503, content={
                "run_id": run_id, "status": "error", "error_code": "retrieval_failed",
                "answer": None, "citations": [],
            })
        if not evidence:
            return {"run_id": run_id, "status": "insufficient_evidence", "answer": None, "citations": []}
        try:
            raw = responder.generate(request.question, evidence)
        except ContextLimitError:
            return JSONResponse(status_code=422, content={
                "run_id": run_id, "status": "error", "error_code": "context_limit_exceeded",
                "answer": None, "citations": [],
            })
        except ValueError:
            logger.exception("Invalid LLM output for run %s", run_id)
            return JSONResponse(status_code=502, content={
                "run_id": run_id, "status": "error", "error_code": "model_output_invalid",
                "answer": None, "citations": [],
            })
        except Exception:
            logger.exception("LLM call failed for run %s", run_id)
            return JSONResponse(status_code=503, content={
                "run_id": run_id, "status": "error", "error_code": "model_failed",
                "answer": None, "citations": [],
            })
        try:
            answer = ModelAnswer.model_validate(raw)
            # Any evidence supplied to the model can affect an answer or refusal,
            # including chunks omitted from its final citation list.
            with lifecycle_lock:
                for item in evidence:
                    current = documents().get(item["document_id"])
                    if not current or current["status"] != "ready" or current["index_version"] != item["index_version"]:
                        return JSONResponse(status_code=409, content={
                            "run_id": run_id, "status": "error", "error_code": "source_changed",
                            "answer": None, "citations": [],
                        })
            if answer.status == "insufficient_evidence":
                if answer.answer is not None or answer.citation_ids:
                    raise ValueError("refusal cannot have answer or citations")
                return {"run_id": run_id, "status": "insufficient_evidence", "answer": None, "citations": []}
            if not answer.answer or not answer.answer.strip() or not answer.citation_ids:
                raise ValueError("answer and citations required")
            by_id = {item["chunk_id"]: item for item in evidence}
            if any(chunk_id not in by_id for chunk_id in answer.citation_ids):
                raise ValueError("citation not in this run's evidence")
            citations = []
            for chunk_id in dict.fromkeys(answer.citation_ids):
                item = by_id[chunk_id]
                citations.append({key: item[key] for key in (
                    "document_id", "chunk_id", "document_title", "source_path",
                    "page_number", "section", "line_start", "chunk_text", "index_version",
                )})
            return {"run_id": run_id, "status": "answered", "answer": answer.answer.strip(), "citations": citations}
        except (ValueError, KeyError):
            logger.warning("Rejected invalid model answer for run %s", run_id, exc_info=True)
            return JSONResponse(status_code=502, content={
                "run_id": run_id, "status": "error", "error_code": "model_output_invalid",
                "answer": None, "citations": [],
            })

    return api


app = create_app()
