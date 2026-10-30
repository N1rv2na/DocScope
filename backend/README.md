# DocScope M1 Backend

This M1 slice accepts UTF-8 Markdown or text-based PDF up to 1 MiB, stores the
original file and document status locally, and writes searchable chunks to Qdrant. A separate
single-question API uses Dense Evidence to answer or abstain with validated
citations. OCR, streaming, and multi-hop are not part of
this slice.

## Start locally

From the repository root in a POSIX shell (for example WSL), use Python 3.10+
and a running Docker daemon:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r backend/requirements-test.txt
docker run --rm --name docscope-qdrant -p 6333:6333 \
  -v docscope-qdrant-data:/qdrant/storage qdrant/qdrant:v1.20.0
```

Run the next commands in a second terminal with the same environment:

```sh
. .venv/bin/activate
export PYTHONPATH=backend/src
export DOCSCOPE_DATA_DIR=data
export QDRANT_URL=http://127.0.0.1:6333
export LLM_BASE_URL=http://127.0.0.1:3425/v1
export LLM_MODEL=group/docscope-free
# Set LLM_API_KEY in this terminal from your local gateway configuration.
python -m uvicorn docscope.api:app --host 127.0.0.1 --port 8000
```

Run one Uvicorn worker for each data directory/Qdrant collection. Lifecycle
operations are serialized within that process. Do not share the same store
between multiple API processes.

PDF text is extracted with pinned `pypdf`; chunks stay within one PDF page and
carry a one-based `page_number`. Markdown chunks retain their heading section
and source line. PDF scans without extractable text, and damaged PDFs, remain
in `failed` state with `parse_failed`; OCR is not supported. PDF extraction is
bounded to 100 pages and 200,000 extracted characters; exceeding either limit
also results in `parse_failed`. The fixed two-page
sample is `backend/tests/fixtures/observatories.pdf` (Orion is on page 2).

The embedding model is `sentence-transformers/all-MiniLM-L6-v2` at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, run on CPU. Its first use
may download weights. The named Qdrant volume preserves the index across
container restarts and must be retained together with the local data directory.
`data/` holds source files and SQLite status and is ignored by Git.
The remote LLM is reached through an OpenAI-compatible URL; the gateway is
external to DocScope. `LLM_API_KEY` stays in the local environment and is never
returned by an API response. If WSL cannot reach a gateway bound to Windows
loopback, configure an accessible HTTPS endpoint or an explicitly exposed
loopback endpoint; a connection error is a model service failure, not an
evidence-insufficient result.

## Replay the fixed sample

```sh
curl -sS -F 'file=@backend/tests/fixtures/telescopes.md;type=text/markdown' \
  http://127.0.0.1:8000/api/documents
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which observatory built the Orion telescope?","limit":2}' \
  http://127.0.0.1:8000/api/retrieval/dense
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which observatory built the Orion telescope?","limit":2,"candidate_limit":20}' \
  http://127.0.0.1:8000/api/retrieval/hybrid
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which observatory built the Orion telescope?","limit":2,"candidate_limit":20,"rerank_limit":20}' \
  http://127.0.0.1:8000/api/retrieval/rerank
curl -sS -H 'Content-Type: application/json' \
  -d '{"question":"Which observatory built the Orion telescope?"}' \
  http://127.0.0.1:8000/api/chat
```

The upload response must have `status: ready` and a `document_id`. The first
Evidence should contain the original Orion sentence, a `chunk_id`, that
`document_id`, and section `Telescope Notes > Orion`. To inspect an import,
request `GET /api/documents/{document_id}`. Failed imports return a
`document_id`, `status: failed`, and an `error_code`; only ready documents are
eligible for retrieval.

To verify PDF page provenance, upload the fixed sample and ask about Orion:

```sh
curl -sS -F 'file=@backend/tests/fixtures/observatories.pdf;type=application/pdf' \
  http://127.0.0.1:8000/api/documents
curl -sS -H 'Content-Type: application/json' \
  -d '{"question":"Which observatory built the Orion telescope?"}' \
  http://127.0.0.1:8000/api/chat
```

The matching citation has `page_number: 2` and the extracted Orion sentence;
Markdown citations have `page_number: null` and retain `section`/`line_start`.
For a deterministic answer without the remote model, run
`PYTHONPATH=backend/src python -m unittest backend.tests.test_pdf_page_citations -v`.

`POST /api/chat` returns `run_id` plus one of three states:

- `answered` (HTTP 200): nonempty `answer` and citations copied from this run's
  retrieved chunks, including source text and location.
- `insufficient_evidence` (HTTP 200): `answer: null`, `citations: []`.
- `error` (HTTP 502 or 503): `error_code` distinguishes invalid model output,
  model failure, and retrieval failure; it is never reported as a successful
  answer. Blank questions fail validation (HTTP 422) before retrieval or LLM.
  Question plus Evidence exceeding 12,000 UTF-8 bytes returns HTTP 422 with
  `context_limit_exceeded`, without a model call. If a retrieved document changed
  during generation, the result is rejected with HTTP 409 `source_changed`.
  This checks every document supplied to the model, including uncited evidence,
  before publishing either an answer or an insufficient-evidence result.

## DOCS-011 single-run LangGraph path

`POST /api/agent/run` accepts the same `{ "question": "..." }` body as
`/api/chat`. It runs a compiled LangGraph with exactly one deterministic
planning step (the standalone question becomes the query), one Hybrid RRF
retrieval of at most five Evidence chunks, and at most one answer call. The
legacy `/api/chat` endpoint remains Dense-only. The Agent path uses Hybrid
without Cross-Encoder because the DOCS-010 dev ablation found that this fixed
reranker configuration reduced evidence coverage; M3's eventual default
retrieval path remains an open evaluation decision.

The response includes `run_id`, the original `question`, `query`,
`retrieved_evidence_ids`, ordered `trace` stages with elapsed times,
`calls` for retrieval and responder invocation attempts, `stop_reason`,
and the existing answer/refusal and citation fields. Captured failures use
`status: error`, a failed trace stage, and `error_code`, with no success answer
or citations. Final Run records are written atomically under
`$DOCSCOPE_DATA_DIR/runs/<run_id>.json`; this local directory is ignored by
Git. A local context check can fail before a remote model request, so
`calls.model` is not a remote HTTP request or Token count. If Run storage
fails, the API returns a failed persistence stage and existing trace but
cannot write a local file. These records contain the question and returned
citation text, so they must not be published without review. This Issue has no second hop, SSE,
conversation memory, or persistent LangGraph Checkpointer.

The fixed API tests use fake retrieval and LLM boundaries, with no remote
model request:

```sh
PYTHONPATH=backend/src python -m unittest backend.tests.test_agent_run -v
```

## Document lifecycle (DOCS-007)

`GET /api/documents` lists processing, ready and failed documents. Uploading
the exact same filename and bytes again returns 200 with the same IDs and no
new chunks. Changed bytes under that filename replace the document. The Web
list also offers explicit replacement via `PUT /api/documents/{document_id}`.
Deletion uses `DELETE /api/documents/{document_id}` and removes source files
and Qdrant points. A missing ID returns 404.

Replacement/deletion makes a document unavailable immediately. Failed
operations stay unavailable even if index cleanup fails; retry an upload or
deletion to recover. The initial store open marks interrupted operations as
failed. Existing databases are migrated without discarding their documents.

Citation checks use `GET /api/documents/{document_id}/citations/{chunk_id}`.
Only `valid` sources may be displayed as current evidence; `invalid` means the
source was replaced, deleted or is unavailable, while HTTP 503 means verification
could not be completed. Full identity, migration and recovery rules are in
[the lifecycle contract](../docs/document-lifecycle.md).

The citation ID check establishes provenance, not factual support of every
sentence. The LLM receives document text as untrusted evidence in the user
message; it is instructed to ignore commands embedded in that text. This is
not a guarantee against all prompt injection or unsupported conclusions.

`/api/retrieval/hybrid` uses the same ready documents and Chunk IDs as Dense.
Qdrant 1.20.0 maintains a `chunk_text` native BM25 index with English `word`
tokenization and lowercase matching; Qdrant RRF merges Dense and BM25 rankings.
`candidate_limit` bounds each branch (1–50, default 20); `limit` bounds the
returned evidence (1–10, default 5). If candidates are scarce, the response can
contain fewer than `limit` items. `/api/chat` remains Dense-only in this
Issue. Replacements and deletions remove points from both retrieval paths.

`/api/retrieval/rerank` scores at most 20 Hybrid RRF candidates with the local
CPU Cross-Encoder `cross-encoder/ms-marco-MiniLM-L6-v2` at revision
`233902d25c440f23af6f7d6e94d2946bac0bee0a`. The `rerank_limit` is 1–20,
at least the final `limit`, and no larger than `candidate_limit`; the final evidence remains limited to
1–10 Chunks (at most 1,000 source characters each). The response retains
`rrf_rank`, `rrf_score`, and the separate raw `rerank_score`; the ordinary
`score` in this endpoint is the rerank score. These score scales must not be
compared directly. Model weights load on the first rerank query; in this
single-user local service, that initial load holds the lifecycle lock and can
delay other requests. `/api/chat`
still uses Dense retrieval at this stage.

## Tests

```sh
PYTHONPATH=backend/src python -m unittest discover -s backend/tests -v
```

By default the API tests use deterministic embedding/index/model doubles and
do not invoke a real model or Qdrant. The optional real Qdrant lifecycle test is
skipped unless `QDRANT_TEST_URL` is set. To run it against Qdrant 1.20.0:

```sh
QDRANT_TEST_URL=http://127.0.0.1:6333 PYTHONPATH=backend/src python -m unittest backend.tests.test_qdrant_lifecycle -v
```

It creates and deletes its own uniquely named test collection and temporary
source directory, leaving the application collection untouched. Embeddings
and answers remain deterministic test doubles; this verifies storage and API
behavior, not model quality. Real model validation is a separate authorized run.
