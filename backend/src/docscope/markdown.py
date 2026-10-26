"""Small Markdown-to-Chunk parser preserving section and original source text."""

from __future__ import annotations

import re


HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
MAX_CHARS = 1000


def markdown_chunks(text: str) -> list[dict]:
    chunks: list[dict] = []
    headings: dict[int, str] = {}
    section_lines: list[tuple[int, str]] = []
    fence_marker: str | None = None

    def flush() -> None:
        if not section_lines:
            return
        body = "".join(line for _, line in section_lines)
        section = " > ".join(headings[level] for level in sorted(headings)) or "Document"
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
                chunks.append({
                    "section": section,
                    "chunk_text": piece,
                    "line_start": section_lines[0][0] + body.count("\n", 0, offset),
                })
            offset = end
        section_lines.clear()

    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if fence_marker is None:
                fence_marker = marker
            elif fence_marker == marker:
                fence_marker = None
        heading = HEADING.match(line) if fence_marker is None and not fence else None
        if heading:
            flush()
            level = len(heading.group(1))
            headings = {key: value for key, value in headings.items() if key < level}
            headings[level] = heading.group(2).strip()
        else:
            section_lines.append((line_number, line))
    flush()
    return chunks
