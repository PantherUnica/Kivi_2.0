"""
Chunking for the episodic half of the RAG corpus.

Dictations are short, so chunks are sentence-grouped with overlap rather than
fixed-width: splitting mid-sentence would break the evidence quotes that the
whole provenance story depends on.
"""
from __future__ import annotations

import re

TARGET_CHARS = 480
OVERLAP_SENTENCES = 1


def chunk(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= TARGET_CHARS:
        return [{"ord": 0, "text": text, "char_start": 0, "char_end": len(text)}]

    spans = [
        (m.group().strip(), m.start(), m.end())
        for m in re.finditer(r"[^.!?\n]+[.!?\n]?", text)
        if m.group().strip()
    ]

    chunks: list[dict] = []
    cur: list[tuple[str, int, int]] = []
    size = 0

    def flush() -> None:
        nonlocal cur, size
        if not cur:
            return
        chunks.append(
            {
                "ord": len(chunks),
                "text": " ".join(s for s, _, _ in cur),
                "char_start": cur[0][1],
                "char_end": cur[-1][2],
            }
        )
        cur = cur[-OVERLAP_SENTENCES:] if OVERLAP_SENTENCES else []
        size = sum(len(s) for s, _, _ in cur)

    for span in spans:
        if size + len(span[0]) > TARGET_CHARS and cur:
            flush()
        cur.append(span)
        size += len(span[0])
    flush()
    return chunks
