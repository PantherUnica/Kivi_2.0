"""
Deterministic, zero-credential LLM stand-in (DECISIONS.md D-08).

This is NOT a canned-answer table. It does real linguistic work:
  * EXTRACT           pattern-based candidate extraction over arbitrary text
  * QUERY_UNDERSTAND  intent / entity / time-window parsing
  * ANSWER            composition strictly from the retrieved context passed in
  * RESTYLE           applies a style rule's directives to a text

It has no knowledge of the evaluation set and no lookup table of questions.
It is weaker than sarvam-m at paraphrase; every eval report says which
provider produced it.
"""
from __future__ import annotations

import json
import re
import time

from app.providers.base import LLMResult, estimate_tokens

# --------------------------------------------------------------- lexicon
# Ordered most-specific first; the first matching family wins for a sentence.

STYLE_PATTERNS = [
    (r"\bno bullet(s| points)?\b", "avoids bullet points"),
    (r"\buse bullet(s| points)?\b", "uses bullet points"),
    (r"\bbullet points?\b", "uses bullet points"),
    (r"\bshort paragraphs?\b", "writes in short paragraphs"),
    (r"\bthree (short )?paragraphs?\b", "keeps it to three short paragraphs"),
    (r"\bkeep (it|them|these) (short|brief|tight|concise)\b", "keeps it short"),
    (r"\b(concise|brief|terse)\b", "prefers concise wording"),
    (r"\bformal\b", "writes formally"),
    (r"\b(casual|informal|relaxed)\b", "writes casually"),
    (r"\bsign(s|ing)? off with\b", "signs off explicitly"),
    (r"\bplain english\b", "writes in plain english"),
    (r"\bno jargon\b", "avoids jargon"),
    (r"\bone[- ]line(r)? summary\b", "opens with a one-line summary"),
    (r"\bacceptance criteria\b", "includes acceptance criteria"),
    (r"\blead with (the )?(outcome|result|ask)\b", "leads with the outcome"),
]

DECISION_PATTERNS = [
    r"\bwe(?:'re| are| have|'ve)? (?:decided|going|moving|switching)\b",
    r"\bwe(?:'ve| have)? (?:chosen|picked|settled on)\b",
    r"\bdecision is\b",
    r"\bwe(?:'re| are) going with\b",
    r"\blet(?:'s| us) go with\b",
    r"\bmoving (?:off|away from|from)\b",
    r"\bi(?:'ve| have)? decided\b",
    r"\bwe(?:'re| are) dropping\b",
]

COMMITMENT_PATTERNS = [
    r"\bi(?:'ll| will| have to| need to)\s+(?:send|share|deliver|get|write|finish|ship|hand over)\b",
    r"\b(?:due|deadline|by)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|eod|end of (?:day|week)|\d{1,2}\s+\w+)\b",
    r"\bi (?:promised|committed|owe)\b",
    r"\bcommitted to\b",
]

ROUTINE_PATTERNS = [
    r"\bevery (?:monday|tuesday|wednesday|thursday|friday|week|morning|sprint)\b",
    r"\bweekly\b",
    r"\beach (?:week|sprint|monday)\b",
    r"\bstand-?up\b",
    r"\bi always\b",
    r"\bmy usual\b",
]

PROJECT_PATTERNS = [
    r"\b(?:project|client|account)\s+([A-Z][A-Za-z0-9]+)",
    r"\b([A-Z][A-Za-z0-9]+)\s+is\s+\w+",
    r"\bworking (?:on|with)\s+([A-Z][A-Za-z0-9]+)",
]

EPHEMERAL_MARKERS = [
    r"\btoday\b", r"\btomorrow\b", r"\bthis (?:morning|afternoon|evening)\b",
    r"\btonight\b", r"\bjust (?:for|this) (?:this|once|time)\b", r"\bright now\b",
    r"\bfor now\b", r"\bthis one time\b", r"\bat the moment\b",
]

INFERENCE_MARKERS = [
    r"\bi think\b", r"\bprobably\b", r"\bi guess\b", r"\bmaybe\b", r"\bperhaps\b",
    r"\bseems? like\b", r"\bi feel like\b", r"\bi suppose\b", r"\bkind of\b",
]

EXPLICIT_MARKERS = [
    r"\bremember (?:that|this)\b", r"\balways\b", r"\bnever\b", r"\bfrom now on\b",
    r"\bgoing forward\b", r"\bmake sure\b", r"\bi prefer\b", r"\bi want\b", r"\bi like\b",
]

# A verb that turns away from a previously stated position.
# A verb that turns away from a previously stated position.
REVERSAL = (
    r"\bmoving (?:off|away from)\b|\boff\b|\bno longer\b|\binstead of\b|"
    r"\bswitch(?:ing|ed)?\b|\bdropp?(?:ing|ed)\b|\bnot any ?more\b|"
    r"\breplac(?:e|ing|ed)\b"
)

NEGATION = r"\bno\b|\bnot\b|\bavoid\b|\bnever\b|\bwithout\b"

CORRECTION_MARKERS = [
    r"\bactually\b", r"\bscratch that\b", r"\bcorrection\b", r"\bi changed my mind\b",
    r"\bnot any ?more\b", r"\bno longer\b", r"\binstead of\b",
]


def _sentences(text: str) -> list[tuple[str, int, int]]:
    """Split into sentences, keeping character offsets for evidence spans."""
    out: list[tuple[str, int, int]] = []
    for m in re.finditer(r"[^.!?\n]+[.!?\n]?", text):
        s = m.group().strip()
        if len(s) >= 8:
            out.append((s, m.start(), m.end()))
    return out


def _any(patterns: list[str], s: str) -> bool:
    return any(re.search(p, s, re.I) for p in patterns)


# Pattern matching is greedy, so "no bullet points" trips BOTH the negative
# rule and the bare "bullet points" rule. A memory that claims the person both
# avoids and uses bullets is worse than no memory at all: the winner of each
# pair is the more specific reading.
CONFLICTS = [
    ("avoids bullet points", "uses bullet points"),
    ("keeps it to three short paragraphs", "writes in short paragraphs"),
    ("keeps it to three short paragraphs", "keeps it short"),
    ("writes formally", "writes casually"),
    ("prefers concise wording", "keeps it short"),
]


def _resolve_conflicts(directives: list[str]) -> list[str]:
    out = list(dict.fromkeys(directives))
    for winner, loser in CONFLICTS:
        if winner in out and loser in out:
            out.remove(loser)
    return out


def _explicitness(sentence: str) -> str:
    """
    Grade how the person said it (D-05).

    A plain declarative statement IS explicit. "We're moving Halo off Postgres"
    is a decision stated outright, not a pattern we inferred from repetition -
    requiring a marker word like "always" before believing someone is just a
    worse reading of what they said.

    `observed_repetition` is assigned by the POLICY engine when a claim is
    corroborated, not by the extractor. The extractor only ever sees one
    utterance, so it is in no position to call something repeated.
    """
    if _any(CORRECTION_MARKERS, sentence):
        return "user_correction"
    if _any(INFERENCE_MARKERS, sentence):
        return "inferred"
    return "explicit_statement"


def _temporality(sentence: str) -> str:
    if _any(EPHEMERAL_MARKERS, sentence):
        return "ephemeral"
    if _any(COMMITMENT_PATTERNS, sentence):
        return "dated_commitment"
    return "durable"


def _extract(text: str, context: dict) -> list[dict]:
    """Propose memory candidates. Deterministic, offset-accurate, no lookups."""
    candidates: list[dict] = []
    app = context.get("app")
    destination = context.get("destination")
    project = context.get("project_hint")
    client = context.get("client")
    audience = client or destination or app or "general writing"

    for sentence, start, end in _sentences(text):
        low = sentence.lower()
        explicitness = _explicitness(sentence)
        temporality = _temporality(sentence)

        # --- style -------------------------------------------------------
        directives = _resolve_conflicts(
            [label for pat, label in STYLE_PATTERNS if re.search(pat, low, re.I)]
        )
        if directives:
            candidates.append(
                {
                    "type": "style_rule",
                    "claim": f"For {audience}: " + ", ".join(sorted(set(directives))),
                    "evidence_quote": sentence,
                    "char_start": start,
                    "char_end": end,
                    "explicitness": explicitness,
                    "temporality": "durable" if temporality != "ephemeral" else "ephemeral",
                    "scope": {"app": app, "destination": destination,
                              "project": project, "client": client},
                    "confidence": 0.8 if explicitness == "explicit_statement" else 0.55,
                }
            )
            continue

        # --- decision ----------------------------------------------------
        if _any(DECISION_PATTERNS, low):
            candidates.append(
                {
                    "type": "decision",
                    "claim": sentence.rstrip(" .!?"),
                    "evidence_quote": sentence,
                    "char_start": start,
                    "char_end": end,
                    "explicitness": explicitness,
                    "temporality": temporality,
                    "scope": {"project": project, "app": app, "destination": None},
                    "confidence": 0.75,
                }
            )
            continue

        # --- commitment --------------------------------------------------
        if _any(COMMITMENT_PATTERNS, low):
            candidates.append(
                {
                    "type": "commitment",
                    "claim": sentence.rstrip(" .!?"),
                    "evidence_quote": sentence,
                    "char_start": start,
                    "char_end": end,
                    "explicitness": explicitness,
                    "temporality": "dated_commitment",
                    "scope": {"project": project, "app": app, "destination": destination},
                    "confidence": 0.7,
                }
            )
            continue

        # --- routine -----------------------------------------------------
        if _any(ROUTINE_PATTERNS, low):
            candidates.append(
                {
                    "type": "routine",
                    "claim": sentence.rstrip(" .!?"),
                    "evidence_quote": sentence,
                    "char_start": start,
                    "char_end": end,
                    "explicitness": explicitness,
                    "temporality": temporality,
                    "scope": {"app": app, "destination": destination, "project": project},
                    "confidence": 0.6,
                }
            )
            continue

        # --- project context ---------------------------------------------
        for pat in PROJECT_PATTERNS:
            m = re.search(pat, sentence)
            if m and len(sentence) < 240:
                candidates.append(
                    {
                        "type": "project_context",
                        "claim": sentence.rstrip(" .!?"),
                        "evidence_quote": sentence,
                        "char_start": start,
                        "char_end": end,
                        "explicitness": explicitness,
                        "temporality": temporality,
                        "scope": {"project": m.group(1) if m.groups() else project},
                        "confidence": 0.6,
                    }
                )
                break

    return candidates[:6]


APPS = ["slack", "gmail", "notion", "linear", "whatsapp", "docs", "email"]

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

INTENTS = [
    (r"\bforget\b|\bstop using\b|\bdelete that\b", "forget_memory"),
    (r"\bactually\b|\bi changed my mind\b|\bnot any ?more\b|\bno longer\b|\bfrom now on\b",
     "correct_memory"),
    (r"\brewrite\b|\bpolish\b|\bredraft\b|\bre-?write\b|\bturn (it|this) into\b|\bdraft\b",
     "restyle"),
    (r"\bfind\b|\bsearch\b|\bshow me\b|\bpull up\b|\bwhich dictation\b", "search_history"),
    # Only the GENERIC ask lists memories. "What do you know about my health?"
    # carries a topic and must go through recall, so that it can abstain.
    (r"\bwhat (?:do you (?:know|remember)|have you learned|did you learn)\b"
     r"(?:\s+about\s+(?:me|us|my work))?\s*[?.!]*$",
     "what_do_you_know"),
]


def _understand(utterance: str, now_iso: str) -> dict:
    from datetime import datetime, timedelta, timezone

    low = utterance.lower()
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)

    intent = "recall"
    for pat, name in INTENTS:
        if re.search(pat, low):
            intent = name
            break

    app = next((a for a in APPS if a in low), None)
    if app == "email":
        app = "gmail"

    # --- resolve a real UTC window from relative language -----------------
    start = end = None
    if "yesterday" in low:
        d = (now - timedelta(days=1)).date()
        start, end = f"{d}T00:00:00+00:00", f"{d}T23:59:59+00:00"
    elif "last week" in low:
        start = (now - timedelta(days=14)).isoformat()
        end = (now - timedelta(days=7)).isoformat()
    elif "this week" in low:
        start = (now - timedelta(days=now.weekday())).isoformat()
        end = now.isoformat()
    else:
        for i, day in enumerate(WEEKDAYS):
            if day in low:
                delta = (now.weekday() - i) % 7 or 7
                d = (now - timedelta(days=delta)).date()
                start, end = f"{d}T00:00:00+00:00", f"{d}T23:59:59+00:00"
                break

    if start and "afternoon" in low:
        start = start[:11] + "12:00:00+00:00"
        end = end[:11] + "18:00:00+00:00"
    elif start and "morning" in low:
        start = start[:11] + "05:00:00+00:00"
        end = end[:11] + "12:00:00+00:00"

    # Capitalised tokens are the best cheap proxy for projects/clients/people.
    entities = [
        t for t in re.findall(r"\b[A-Z][A-Za-z0-9]{2,}\b", utterance)
        if t.lower() not in {"hey", "kivi", "slack", "gmail", "notion", "linear", "whatsapp"}
    ]

    # Which KIND of memory the question reaches for. A strong relevance signal
    # downstream: "what did we decide" is answered by a decision, even though
    # the word "decide" appears nowhere inside the decision itself.
    types: list[str] = []
    if re.search(
        r"\bstyle\b|\btone\b|\bvoice\b|\bformat\b|\bphrase\b|\brewrite\b|"
        r"\bdraft\b|\bhow (?:should|do|did|would) i (?:write|phrase|word|draft|put)\b|"
        r"\bhow i write\b|\bwrite (?:the |my |a )?(?:next|update|email|note)\b",
        low,
    ):
        types.append("style_rule")
    if re.search(r"\bdecide\b|\bdecision\b|\bchose\b|\bchoice\b|\bwent with\b", low):
        types.append("decision")
    if re.search(r"\bowe\b|\bdue\b|\bdeadline\b|\bpromis|\bcommit", low):
        types.append("commitment")
    if re.search(r"\broutine\b|\bevery (?:monday|week)\b|\busually\b", low):
        types.append("routine")

    return {
        "intent": intent,
        "app": app,
        "entities": entities[:6],
        "time_window": {"start": start, "end": end} if start else None,
        "memory_types": types,
        "needs_memory": intent != "search_history",
    }


STOP = {
    # articles, pronouns, auxiliaries
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by",
    "from", "into", "over", "under", "than", "then", "there", "their", "them", "they",
    "i", "my", "me", "you", "your", "we", "our", "us", "it", "its", "that", "this",
    "these", "those", "is", "was", "are", "were", "be", "been", "being", "am",
    "do", "does", "did", "done", "have", "has", "had", "can", "could", "would",
    "should", "will", "shall", "may", "might", "must",
    # question scaffolding - these say nothing about the SUBJECT of the question,
    # and counting them as content made real questions look uncovered
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "know", "knows", "remember", "remembers", "recall", "learn", "learned",
    "tell", "told", "say", "said", "says", "about", "anything", "something",
    "much", "many", "any", "some", "all", "more", "most", "just", "like",
    "hey", "kivi", "please", "thing", "things", "stuff",
}


def _stem(word: str) -> str:
    """
    Crude suffix stripping. Enough to make "budgeted" match "budget" and
    "meetings" match "meeting", which is what term overlap actually needs.
    """
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ied", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            base = word[: -len(suffix)]
            return base + "y" if suffix in ("ies", "ied") else base
    return word


def _terms(text: str) -> set[str]:
    return {
        _stem(w)
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in STOP and len(w) > 2
    }


def _answer(question: str, context: list[dict], wanted_types: list[str] | None = None) -> str:
    """
    Compose an answer using ONLY the supplied context, with citations.

    No context means no answer - the abstention gate upstream should have
    caught that already, but this is the second net.
    """
    if not context:
        return "I don't have anything in your history about that."

    q = _terms(question)
    scored = []
    for item in context:
        overlap = len(q & _terms(item.get("text", "")))
        scored.append((overlap, item))
    scored.sort(key=lambda x: -x[0])

    # Coverage test. Retrieval can hand back context that merely shares a
    # subject with the question ("Ember") while containing nothing that answers
    # it ("which CMS vendor"). Measure how much of the question's content is
    # actually present before composing anything.
    context_terms: set[str] = set()
    for item in context:
        context_terms |= _terms(item.get("text", ""))
    covered = len(q & context_terms) / max(1, len(q))

    # A question that explicitly asks for a KIND of memory ("what did we
    # decide...") is already telling us what is relevant. If a memory of that
    # type came back, term overlap is the wrong test - "decide" will never
    # appear inside the decision itself.
    wanted = set(wanted_types or [])
    type_match = [it for it in context if it.get("type") and it["type"] in wanted]

    picked = [it for score, it in scored[:3] if score > 0]
    if type_match:
        picked = type_match[:2] + [p for p in picked if p not in type_match][:1]
    elif not picked or covered < 0.4:
        return "I don't have anything in your history about that."


    parts = []
    for it in picked:
        text = (it.get("text") or "").strip().rstrip(".")
        parts.append(f"{text} [{it['id']}]")
    return ". ".join(parts) + "."


RESTYLE_RULES = {
    "avoids bullet points": "unbullet",
    "uses bullet points": "bullet",
    "writes in short paragraphs": "shorten",
    "keeps it to three short paragraphs": "three_paras",
    "keeps it short": "shorten",
    "prefers concise wording": "shorten",
    "writes formally": "formal",
    "writes casually": "casual",
    "opens with a one-line summary": "summary_first",
}


def _restyle(text: str, directives: list[str]) -> str:
    """Apply style directives mechanically. Visible, checkable transformations."""
    out = text.strip()
    ops = {RESTYLE_RULES.get(d, "") for d in directives}

    if "unbullet" in ops:
        lines = [re.sub(r"^\s*[-*•]\s*", "", ln).strip() for ln in out.splitlines()]
        out = " ".join(ln for ln in lines if ln)
    if "bullet" in ops:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", out) if s.strip()]
        out = "\n".join(f"- {s}" for s in sents)
    if "shorten" in ops or "three_paras" in ops:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", out) if s.strip()]
        if "three_paras" in ops and len(sents) > 3:
            size = max(1, len(sents) // 3)
            out = "\n\n".join(
                " ".join(sents[i : i + size]) for i in range(0, len(sents), size)
            )[:2000]
        else:
            out = " ".join(sents[: max(3, len(sents) // 2)])
    if "formal" in ops:
        for a, b in [("don't", "do not"), ("can't", "cannot"), ("we'll", "we will"),
                     ("it's", "it is"), ("I'm", "I am"), ("won't", "will not")]:
            out = out.replace(a, b)
    return out.strip()


class MockLLM:
    """Deterministic provider. Dispatches on a TASK marker in the system prompt."""

    name = "mock"

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult:
        started = time.perf_counter()
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {"text": user}

        if "TASK: EXTRACT" in system:
            result = {"candidates": _extract(payload.get("text", ""), payload.get("context", {}))}
        elif "TASK: QUERY_UNDERSTANDING" in system:
            result = _understand(payload.get("utterance", ""), payload.get("now", ""))
        elif "TASK: ANSWER" in system:
            result = {
                "answer": _answer(
                    payload.get("question", ""),
                    payload.get("context", []),
                    payload.get("wanted_types") or [],
                )
            }
        elif "TASK: RESTYLE" in system:
            result = {
                "text": _restyle(payload.get("text", ""), payload.get("directives", []))
            }
        elif "TASK: CONTRADICTION" in system:
            a, b = payload.get("claim_a", ""), payload.get("claim_b", "")
            ta, tb = _terms(a), _terms(b)
            shared = ta & tb
            overlap = len(shared) / max(1, min(len(ta), len(tb)))

            # A reversal verb in the NEWER claim plus a shared subject is the
            # clearest signal available without a real model: "we're moving
            # Halo OFF Postgres" reverses "we're going with Postgres for Halo".
            reversal = bool(re.search(REVERSAL, b.lower())) and not re.search(REVERSAL, a.lower())
            polarity = bool(re.search(NEGATION, a.lower())) != bool(re.search(NEGATION, b.lower()))

            contradicts = (reversal and len(shared) >= 2) or (polarity and overlap > 0.3)
            result = {
                "contradicts": bool(contradicts),
                "overlap": round(overlap, 3),
                "reversal": reversal,
                "shared": sorted(shared)[:6],
            }
        else:
            result = {"text": ""}

        text = json.dumps(result)
        return LLMResult(
            text=text,
            model="mock-deterministic",
            prompt_tokens=estimate_tokens(system + user),
            completion_tokens=estimate_tokens(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=0.0,
        )
