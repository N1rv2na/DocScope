# DOCS-003 Markdown → Dense Evidence

This slice accepts UTF-8 Markdown up to 1 MiB, stores the original file and
document status locally, and writes searchable chunks to Qdrant. It does not
generate answers or handle PDF, replacement, or deletion yet.

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
python -m uvicorn docscope.api:app --host 127.0.0.1 --port 8000
```

The embedding model is `sentence-transformers/all-MiniLM-L6-v2` at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, run on CPU. Its first use
may download weights. The named Qdrant volume preserves the index across
container restarts and must be retained together with the local data directory.
`data/` holds source files and SQLite status and is ignored by Git.

## Replay the fixed sample

```sh
curl -sS -F 'file=@backend/tests/fixtures/telescopes.md;type=text/markdown' \
  http://127.0.0.1:8000/api/documents
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which observatory built the Orion telescope?","limit":2}' \
  http://127.0.0.1:8000/api/retrieval/dense
```

The upload response must have `status: ready` and a `document_id`. The first
Evidence should contain the original Orion sentence, a `chunk_id`, that
`document_id`, and section `Telescope Notes > Orion`. To inspect an import,
request `GET /api/documents/{document_id}`. Failed imports return a
`document_id`, `status: failed`, and an `error_code`; only ready documents are
eligible for retrieval.

## Tests

```sh
PYTHONPATH=backend/src python -m unittest discover -s backend/tests -v
```

The API tests use a deterministic embedding/index double and do not invoke a
real model or Qdrant. A real model and Qdrant run is a separate manual check.
