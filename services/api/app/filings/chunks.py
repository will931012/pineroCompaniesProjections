"""Split section text into passages for search and citation.

Passages follow paragraph boundaries and aim for 1,000–2,000 characters; a paragraph longer
than the maximum is split at sentence ends (and a single overlong sentence at a space).
Offsets index into the section text, so a citation can point at the exact span.
"""

import re
from dataclasses import dataclass
from itertools import pairwise

TARGET_CHARS = 1000
MAX_CHARS = 2000
_SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(“\"])")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    char_start: int
    char_end: int


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    segment = text[start:end]
    return start + len(segment) - len(segment.lstrip()), start + len(segment.rstrip())


def _pieces(text: str) -> list[tuple[int, int]]:
    """Spans of one paragraph, each at most MAX_CHARS."""
    if len(text) <= MAX_CHARS:
        return [(0, len(text))]
    bounds = [0, *(m.end() for m in _SENTENCE_BREAK.finditer(text)), len(text)]
    spans: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end in pairwise(bounds):
        while end - start > MAX_CHARS:
            cut = text.rfind(" ", start + 1, start + MAX_CHARS)
            cut = cut if cut > start else start + MAX_CHARS
            if current is not None:
                spans.append(current)
                current = None
            spans.append((start, cut))
            start = cut
        if current is not None and end - current[0] <= MAX_CHARS:
            current = (current[0], end)
        else:
            if current is not None:
                spans.append(current)
            current = (start, end)
    if current is not None:
        spans.append(current)
    return [_trimmed(text, s, e) for s, e in spans if text[s:e].strip()]


def chunk_section(text: str) -> list[Chunk]:
    spans: list[tuple[int, int]] = []
    position = 0
    for paragraph in text.split("\n\n"):
        if paragraph.strip():
            spans.extend((position + s, position + e) for s, e in _pieces(paragraph))
        position += len(paragraph) + 2

    chunks: list[Chunk] = []
    current: tuple[int, int] | None = None
    for span in spans:
        if current is None:
            current = span
        elif current[1] - current[0] < TARGET_CHARS and span[1] - current[0] <= MAX_CHARS:
            current = (current[0], span[1])
        else:
            chunks.append(Chunk(len(chunks), text[current[0] : current[1]], *current))
            current = span
    if current is not None:
        chunks.append(Chunk(len(chunks), text[current[0] : current[1]], *current))
    return chunks
