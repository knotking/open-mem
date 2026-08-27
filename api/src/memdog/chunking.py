"""Chunking, with offsets back into the source.

The offsets are not decoration: `span_start` / `span_end` are what let a
citation open at the sentence, and what keeps citations working later when
compression archives the original.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNKER_VERSION = "paragraph-1"

_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    span_start: int
    span_end: int


def _segments(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for match in _PARAGRAPH.finditer(text):
        if match.start() > pos:
            spans.append((pos, match.start()))
        pos = match.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans or [(0, len(text))]


def chunk_text(text: str, *, max_chars: int, overlap: int) -> list[Chunk]:
    if not text.strip():
        return []
    chunks: list[Chunk] = []
    start = end = None

    def flush() -> None:
        nonlocal start, end
        if start is None or end is None:
            return
        body = text[start:end]
        if body.strip():
            chunks.append(Chunk(len(chunks), body, start, end))
        start = end = None

    for seg_start, seg_end in _segments(text):
        # A single oversized paragraph is windowed rather than truncated.
        if seg_end - seg_start > max_chars:
            flush()
            pos = seg_start
            while pos < seg_end:
                stop = min(pos + max_chars, seg_end)
                body = text[pos:stop]
                if body.strip():
                    chunks.append(Chunk(len(chunks), body, pos, stop))
                if stop >= seg_end:
                    break
                pos = stop - overlap if stop - overlap > pos else stop
            continue
        if start is None:
            start, end = seg_start, seg_end
        elif seg_end - start <= max_chars:
            end = seg_end
        else:
            flush()
            start, end = seg_start, seg_end
    flush()
    return chunks
