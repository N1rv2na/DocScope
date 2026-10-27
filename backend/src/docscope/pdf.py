"""Extract bounded, page-local text chunks from text-based PDFs."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from docscope.markdown import MAX_CHARS

MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARS = 200_000


def pdf_chunks(content: bytes) -> list[dict]:
    reader = PdfReader(BytesIO(content))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError("PDF has too many pages")
    chunks: list[dict] = []
    extracted_chars = 0
    for page_number, page in enumerate(reader.pages, start=1):
        body = page.extract_text() or ""
        extracted_chars += len(body)
        if extracted_chars > MAX_EXTRACTED_CHARS:
            raise ValueError("PDF has too much extractable text")
        offset = 0
        while offset < len(body):
            while offset < len(body) and body[offset].isspace():
                offset += 1
            if offset >= len(body):
                break
            end = min(offset + MAX_CHARS, len(body))
            if end < len(body):
                boundary = max(body.rfind(" ", offset, end), body.rfind("\n", offset, end))
                if boundary > offset:
                    end = boundary
            piece = body[offset:end].rstrip()
            if piece:
                chunks.append({"page_number": page_number, "chunk_text": piece})
            offset = end
    return chunks
