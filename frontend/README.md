# DocScope Web (DOCS-005)

This M1 page supports UTF-8 Markdown and text-based PDF upload, one independent
English question, and expanding the original Chunk cited by an answer. PDF
citations show a one-based page number; Markdown citations show section and
line. It does not implement OCR, document replacement/deletion, sessions,
streaming, or Agent trace.

## Local start

Use Node.js 20.19+ (or 22.12+) from the repository root. Start Qdrant and the
FastAPI backend as described in [backend/README.md](../backend/README.md).
The backend must listen on `127.0.0.1:8000`; only the backend receives LLM
settings and credentials.

In a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/`. Vite forwards `/api/*` requests to the local
FastAPI service, so no browser LLM credentials or extra CORS configuration are
needed. Upload `backend/tests/fixtures/telescopes.md` (from the repository
root), ask “Which observatory built the Orion telescope?”, and expand the
source under the answer.
For a PDF check, upload `backend/tests/fixtures/observatories.pdf` and ask the
same Orion question. The citation should show page 2 and the Orion sentence.
Damaged or image-only PDFs should display a failed import, not "索引就绪".

## Verification

```sh
cd frontend
npx playwright install chromium
npm run build
npm run test:e2e
```

The browser tests replace API responses and do not contact Qdrant or a real
LLM. They cover the upload/question/citation flow, Tab and Enter interaction,
visible keyboard focus, input errors, indexing failure, model failure, PDF page
display, and evidence-insufficient state. Running the local start flow against FastAPI is a
separate integration check.
