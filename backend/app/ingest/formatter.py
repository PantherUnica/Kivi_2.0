"""
Dictation formatting: raw ASR -> written text.

This is Kivi's first job, before any memory exists. The browser's recogniser
hands over lowercased, unpunctuated speech; Kivi turns it into writing. The
model does it when one is configured; the deterministic fallback does the
safe, mechanical part (sentences, capitals, "i" -> "I", spoken punctuation).

Memory never touches this step (D-19). Styles would; this is where they
would plug in.
"""
from __future__ import annotations

import json
import re

from app.providers import get_llm

FORMAT_SYSTEM = """TASK: FORMAT_DICTATION

Turn raw speech-recogniser output into clean written text. Add punctuation
and capitalisation, honour spoken punctuation ("full stop", "comma",
"new line"), fix obvious recogniser slips only when unambiguous, and change
NOTHING else - no rewording, no summarising, no additions.

Return ONLY JSON: {"text": "..."}"""

SPOKEN = [
    (r"\s*\bfull stop\b\s*", ". "), (r"\s*\bperiod\b\s*", ". "),
    (r"\s*\bcomma\b\s*", ", "), (r"\s*\bquestion mark\b\s*", "? "),
    (r"\s*\bexclamation mark\b\s*", "! "), (r"\s*\bnew line\b\s*", "\n"),
    (r"\s*\bnew paragraph\b\s*", "\n\n"), (r"\s*\bcolon\b\s*", ": "),
]


def mechanical(raw: str) -> str:
    """The deterministic formatter. Never invents; only punctuates."""
    text = raw.strip()
    if not text:
        return ""
    for pat, rep in SPOKEN:
        text = re.sub(pat, rep, text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,?!:])", r"\1", text)

    # Sentence boundaries: split on terminal punctuation; if the recogniser
    # gave none, treat the whole thing as one sentence rather than guessing.
    parts = re.split(r"(?<=[.?!])\s+", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p = p[0].upper() + p[1:]
        p = re.sub(r"\bi\b", "I", p)
        p = re.sub(r"\bi'(m|ll|ve|d)\b", lambda m: "I'" + m.group(1), p)
        if p[-1] not in ".?!":
            p += "."
        out.append(p)
    return " ".join(out)


def format_dictation(raw: str) -> tuple[str, dict]:
    """Returns (formatted_text, meter). Falls back to mechanical on any failure."""
    base = mechanical(raw)
    llm = get_llm()
    if getattr(llm, "name", "") == "mock":
        return base, {"model": "mechanical", "tokens": 0, "cost_usd": 0.0}
    try:
        res = llm.complete(FORMAT_SYSTEM, json.dumps({"raw": raw}), json_mode=True)
        parsed = res.json() or {}
        text = (parsed.get("text") or "").strip()
        if text:
            return text, {"model": res.model, "tokens": res.prompt_tokens + res.completion_tokens,
                          "cost_usd": res.cost_usd}
    except Exception:  # noqa: BLE001 - a formatting failure must not lose the dictation
        pass
    return base, {"model": "mechanical-fallback", "tokens": 0, "cost_usd": 0.0}
