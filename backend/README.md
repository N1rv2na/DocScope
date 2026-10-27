# DocScope M1 Backend

This M1 slice accepts UTF-8 Markdown up to 1 MiB, stores the original file and
document status locally, and writes searchable chunks to Qdrant. A separate
single-question API uses Dense Evidence to answer or abstain with validated
citations. PDF, replacement, deletion, streaming, and multi-hop are not part of
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
  -d '{"question":"Which observatory built the Orion telescope?"}' \
  http://127.0.0.1:8000/api/chat
```

The upload response must have `status: ready` and a `document_id`. The first
Evidence should contain the original Orion sentence, a `chunk_id`, that
`document_id`, and section `Telescope Notes > Orion`. To inspect an import,
request `GET /api/documents/{document_id}`. Failed imports return a
`document_id`, `status: failed`, and an `error_code`; only ready documents are
eligible for retrieval.

`POST /api/chat` returns `run_id` plus one of three states:

- `answered` (HTTP 200): nonempty `answer` and citations copied from this run's
  retrieved chunks, including source text and location.
- `insufficient_evidence` (HTTP 200): `answer: null`, `citations: []`.
- `error` (HTTP 502 or 503): `error_code` distinguishes invalid model output,
  model failure, and retrieval failure; it is never reported as a successful
  answer. Blank questions fail validation (HTTP 422) before retrieval or LLM.
  Question plus Evidence exceeding 12,000 UTF-8 bytes returns HTTP 422 with
  `context_limit_exceeded`, without a model call.

The citation ID check establishes provenance, not factual support of every
sentence. The LLM receives document text as untrusted evidence in the user
message; it is instructed to ignore commands embedded in that text. This is
not a guarantee against all prompt injection or unsupported conclusions.

## Tests

```sh
PYTHONPATH=backend/src python -m unittest discover -s backend/tests -v
```

The API tests use deterministic embedding/index/model doubles and do not invoke
a real model or Qdrant. A real model and Qdrant run is a separate manual check.
