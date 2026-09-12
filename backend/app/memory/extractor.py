"""
Candidate extraction.

The model's only job is to PROPOSE. It never decides what is kept - the policy
engine does that (D-13). Output is validated against a strict schema; anything
malformed is dropped with a logged reason rather than guessed at.
"""
from __future__ import annotations

import json
import logging

from app.models import EXPLICITNESS, MEMORY_TYPES
from app.providers import LLMResult, get_llm

log = logging.getLogger(__name__)

EXTRACT_SYSTEM = """TASK: EXTRACT_CANDIDATES

You read one voice dictation and propose durable memory candidates for a
voice-first assistant called Kivi.

Kivi remembers only what the person SAID and HOW they write. It never models
who they are. Propose candidates of these types only:

  style_rule       how this person wants writing done, for a given destination
  decision         a choice that was made and will still matter later
  commitment       something promised, with a due date if one was stated
  project_context  a durable fact about a project, client or piece of work
  routine          a recurring way of working

Rules you must follow:
  - Quote evidence VERBATIM from the input. Never paraphrase a quote.
  - If something is true only today or only once, mark temporality "ephemeral".
  - If the person hedged ("I think", "probably", "maybe"), mark explicitness
    "inferred". Do not upgrade a hedge into a statement.
  - Never propose anything about health, money, relationships or emotional
    state. If you see such material, skip it silently.
  - Propose nothing rather than something weak. Most dictations contain no
    durable memory at all, and that is the expected outcome.

Return ONLY JSON:
{"candidates":[{"type":..., "claim":..., "evidence_quote":...,
  "explicitness":"explicit_statement|observed_repetition|inferred|user_correction",
  "temporality":"durable|ephemeral|dated_commitment",
  "scope":{"app":...,"destination":...,"project":...},
  "confidence":0.0-1.0}]}"""

CONTRADICTION_SYSTEM = """TASK: CONTRADICTION_CHECK

Two claims from the same person about the same subject. Decide whether the
second CONTRADICTS the first, or merely restates/extends it.

Return ONLY JSON: {"contradicts": true|false, "why": "one short sentence"}"""


def _valid(candidate: dict) -> tuple[bool, str]:
    if not isinstance(candidate, dict):
        return False, "not an object"
    if candidate.get("type") not in MEMORY_TYPES:
        return False, f"unknown type {candidate.get('type')!r}"
    claim = (candidate.get("claim") or "").strip()
    if len(claim) < 8:
        return False, "claim too short to be useful"
    if len(claim) > 400:
        return False, "claim too long to be a single claim"
    if not (candidate.get("evidence_quote") or "").strip():
        return False, "no evidence quote"
    if candidate.get("explicitness") not in EXPLICITNESS:
        candidate["explicitness"] = "observed_repetition"
    return True, ""


def extract(text: str, context: dict) -> tuple[list[dict], list[dict], LLMResult]:
    """
    Returns (valid_candidates, rejected_with_reasons, llm_result).

    Rejections are returned rather than swallowed so they can be written to
    memory_decisions - a candidate dropped for being malformed is still a
    decision the system made.
    """
    llm = get_llm()
    payload = json.dumps({"text": text, "context": context})
    result = llm.complete(EXTRACT_SYSTEM, payload, json_mode=True)

    parsed = result.json()
    if not parsed:
        log.warning("extractor: unparseable model output (%d chars)", len(result.text))
        return [], [{"candidate": {}, "reason": "model returned unparseable output"}], result

    raw = parsed.get("candidates", parsed if isinstance(parsed, list) else [])
    if not isinstance(raw, list):
        return [], [{"candidate": {}, "reason": "candidates was not a list"}], result

    good, bad = [], []
    for cand in raw[:8]:
        ok, why = _valid(cand)
        if ok:
            # Anchor the quote to real offsets; a quote we cannot locate in the
            # source is not evidence, however plausible it reads.
            quote = cand["evidence_quote"].strip()
            idx = text.find(quote)
            if idx == -1:
                idx = text.lower().find(quote.lower()[:60])
            if idx == -1:
                bad.append({"candidate": cand, "reason": "evidence quote not found in source"})
                continue
            cand["char_start"] = idx
            cand["char_end"] = idx + len(quote)
            good.append(cand)
        else:
            bad.append({"candidate": cand, "reason": why})

    return good, bad, result


def make_contradiction_checker():
    """Returns a callable(claim_a, claim_b) -> bool backed by the LLM."""
    llm = get_llm()

    def check(claim_a: str, claim_b: str) -> bool:
        try:
            res = llm.complete(
                CONTRADICTION_SYSTEM,
                json.dumps({"claim_a": claim_a, "claim_b": claim_b}),
                json_mode=True,
            )
            parsed = res.json() or {}
            return bool(parsed.get("contradicts"))
        except Exception as exc:  # noqa: BLE001
            # Fail closed: if we cannot tell, do not silently overwrite memory.
            log.error("contradiction check failed (%s); treating as NOT contradictory", exc)
            return False

    return check
