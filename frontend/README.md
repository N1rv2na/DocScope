# DocScope M1 Web

This M1 page supports UTF-8 Markdown and text-based PDF upload, one independent
English question, and expanding the original Chunk cited by an answer. PDF
citations show a one-based page number; Markdown citations show section and
line. The document list supports status refresh, replacement and deletion;
old answer citations are revalidated before displaying source text. OCR,
sessions, streaming and Agent trace are not implemented.

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

For lifecycle validation, upload the same filename/content twice and check
that the list and chunk count do not grow. Keep an answer open, select that
document's replacement action, choose changed content and submit. The old
answer remains visible, but its citation reports “引用已失效”. Ask again to
see current evidence, then delete the document and check that its citation is
invalid too. A failed deletion stays visible and can be retried. Refreshing
the list or returning to the browser window rechecks open citations; a failed
verification is shown separately from a definitively invalid source.

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
display, lifecycle operations, old-citation invalidation and evidence-insufficient state. Running the local start flow against FastAPI is a
separate integration check.
