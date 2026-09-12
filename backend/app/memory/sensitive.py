"""
The sensitivity fence (DECISIONS.md D-06).

Four categories are excluded from memory DERIVATION entirely. The interaction
itself is still stored verbatim - it is the user's own transcript - but no
memory may be distilled from a fenced span, and the refusal is logged with the
category only, never the text.

Survey basis (n=100, research/kivi_survey_analysis.md):
    relationship conflicts  67% "never auto-capture"   (professionals: 87%)
    emotional/mental state  51%
    financial situation     44%                        (professionals: 67%)
    health/medical          42%

The vision document commits to the stricter professional threshold.
"""
from __future__ import annotations

import hashlib
import re

# Ordered: a span is attributed to the first category that matches.
FENCE = {
    "health": [
        r"\bdoctor(?:'s)?\b", r"\bdentist\b", r"\bclinic\b", r"\bhospital\b",
        r"\bdiagnos(?:is|ed)\b", r"\bsurgery\b", r"\bmedication\b", r"\bprescription\b",
        r"\bblood (?:test|pressure|work)\b", r"\bscan\b", r"\btherapy\b", r"\btherapist\b",
        r"\bmigraine\b", r"\bsick leave\b", r"\bmedical\b", r"\bsymptom\b",
    ],
    "financial": [
        r"\bsalary\b", r"\bpay ?rise\b", r"\braise\b(?!\s+(?:a|the)\s+(?:point|issue|concern))",
        r"\bappraisal\b", r"\bbonus\b", r"\bloan\b", r"\bemi\b", r"\bmortgage\b",
        r"\bdebt\b", r"\bcredit card\b", r"\bmy (?:rent|savings|bank)\b", r"\bin ?hand\b",
        r"\bctc\b", r"\bcompensation\b",
    ],
    "relationship_conflict": [
        r"\bargument\b", r"\bfell out\b", r"\bconflict with\b", r"\bnot speaking\b",
        r"\bshouted\b", r"\bblew up at\b", r"\bpassive.aggressive\b", r"\btension with\b",
        r"\bmy (?:partner|wife|husband|boyfriend|girlfriend)\b", r"\bbreak ?up\b",
        r"\bdivorce\b", r"\bhr complaint\b", r"\bwent behind my back\b",
    ],
    "emotional_state": [
        r"\banxious\b", r"\banxiety\b", r"\bdepress(?:ed|ion)\b", r"\bburn(?:t|ed) out\b",
        r"\bburnout\b", r"\boverwhelmed\b", r"\bcrying\b", r"\bcried\b", r"\bpanic\b",
        r"\bexhausted\b", r"\bmiserable\b", r"\bstruggling mentally\b", r"\blow point\b",
    ],
}

# The one documented escape hatch (D-06). 54% of respondents said sensitive
# work topics are acceptable "only if I say remember this".
EXPLICIT_REMEMBER = re.compile(
    r"\b(?:remember (?:this|that)|make a note of this|keep this in mind|save this)\b", re.I
)

_COMPILED = {cat: [re.compile(p, re.I) for p in pats] for cat, pats in FENCE.items()}


def span_hash(text: str) -> str:
    """Stable identifier for a fenced span. The text itself is never stored."""
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:32]


def classify(text: str) -> str | None:
    """Return the fenced category this text belongs to, or None."""
    for category, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            return category
    return None


def scan(text: str) -> list[dict]:
    """
    Find every fenced sentence in a transcript.

    Returns one record per sentence, carrying the category, the offsets, a hash,
    and whether the user explicitly asked for it to be remembered.
    """
    found: list[dict] = []
    for m in re.finditer(r"[^.!?\n]+[.!?\n]?", text):
        sentence = m.group().strip()
        if len(sentence) < 6:
            continue
        category = classify(sentence)
        if category:
            found.append(
                {
                    "category": category,
                    "char_start": m.start(),
                    "char_end": m.end(),
                    "span_hash": span_hash(sentence),
                    "explicit_remember": bool(EXPLICIT_REMEMBER.search(sentence)),
                    "length": len(sentence),
                }
            )
    return found


def redact(text: str) -> tuple[str, list[dict]]:
    """
    Blank out fenced sentences before the extractor ever sees them.

    This is the load-bearing half of the fence: the model is not asked to
    behave, it is simply never shown the material. Offsets are preserved so
    evidence spans from the surviving text stay accurate.
    """
    spans = scan(text)
    if not spans:
        return text, []

    chars = list(text)
    for s in spans:
        if s["explicit_remember"]:
            continue  # handled by policy R1 as ASK_CONFIRMATION, not silently dropped
        for i in range(s["char_start"], min(s["char_end"], len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars), spans
