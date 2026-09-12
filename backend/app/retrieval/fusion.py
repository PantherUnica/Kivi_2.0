"""
Fusion and reranking (DECISIONS.md D-16, stage 3).

Reciprocal Rank Fusion across channels, then a deterministic rescore:

    final = RRF
          + w_conf   * confidence
          + w_recency* recency_weight(type, last_confirmed_at)
          + w_scope  * scope_match
          - p_inactive * (status != 'active')

Every term is persisted in `retrieval_traces.score_breakdown`, so "why was this
ranked above that?" is answerable from the database without rerunning anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.config import settings
from app.memory.confidence import recency_weight
from app.retrieval.channels import Candidate


@dataclass
class Fused:
    kind: str
    ref_id: str
    text: str
    channels: list[str] = field(default_factory=list)
    best_similarity: float = 0.0   # max cosine from any dense channel
    lexical_hit: bool = False
    rrf: float = 0.0
    final: float = 0.0
    breakdown: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)
    included: bool = True
    exclusion_reason: str | None = None


def _scope_match(payload: dict, intent: dict) -> float:
    scope = payload.get("scope") or {}
    want_app = (intent.get("app") or "").lower()
    entities = [e.lower() for e in (intent.get("entities") or [])]

    score = 0.0
    have_app = (scope.get("app") or payload.get("app") or "").lower()
    if want_app and have_app and want_app == have_app:
        score += 0.6

    haystack = " ".join(
        str(v).lower() for v in [
            scope.get("destination"), scope.get("project"),
            payload.get("destination"), payload.get("project_hint"),
        ] if v
    )
    if entities and any(e in haystack for e in entities):
        score += 0.4
    return min(1.0, score)


def fuse(candidates: list[Candidate], intent: dict, now: datetime | None = None,
         superseded_sources: set[str] | None = None) -> list[Fused]:
    k = settings.rag_rrf_k
    superseded_sources = superseded_sources or set()
    merged: dict[tuple[str, str], Fused] = {}

    for cand in candidates:
        key = (cand.kind, cand.ref_id)
        item = merged.get(key)
        if item is None:
            item = Fused(kind=cand.kind, ref_id=cand.ref_id, text=cand.text,
                         payload=cand.payload)
            merged[key] = item
        item.channels.append(cand.channel)
        item.rrf += 1.0 / (k + cand.rank + 1)

        # Dense channels report a cosine; keep the best one, because how well a
        # candidate actually matches the QUESTION has to outrank how confident
        # we are about the candidate in the abstract.
        if cand.channel in ("semantic", "episodic"):
            item.best_similarity = max(item.best_similarity, cand.raw_score)
        elif cand.channel == "lexical":
            item.lexical_hit = True

    out: list[Fused] = []
    for item in merged.values():
        p = item.payload
        confidence = float(p.get("confidence") or 0.0)
        status = p.get("status") or "active"
        mem_type = p.get("type") or "episode"

        rec = recency_weight(mem_type, p.get("last_confirmed_at"), now) if item.kind == "memory" \
            else 0.5
        scope = _scope_match(p, intent)
        inactive_penalty = settings.rag_p_inactive if status != "active" else 0.0

        final = (
            item.rrf
            + settings.rag_w_similarity * item.best_similarity
            + settings.rag_w_confidence * confidence
            + settings.rag_w_recency * rec
            + settings.rag_w_scope * scope
            - inactive_penalty
        )
        item.final = round(final, 6)
        item.breakdown = {
            "rrf": round(item.rrf, 6),
            "similarity": round(item.best_similarity, 4),
            "w_similarity": settings.rag_w_similarity,
            "lexical_hit": item.lexical_hit,
            "channels": sorted(set(item.channels)),
            "confidence": round(confidence, 3),
            "w_confidence": settings.rag_w_confidence,
            "recency": round(rec, 4),
            "w_recency": settings.rag_w_recency,
            "scope_match": round(scope, 3),
            "w_scope": settings.rag_w_scope,
            "status": status,
            "inactive_penalty": inactive_penalty,
        }

        # Provisional memories are known but not usable (D-05). They are
        # retrieved and then visibly excluded, never silently dropped.
        if item.kind == "memory" and status == "provisional":
            item.included = False
            item.exclusion_reason = (
                "provisional: held as an inference, not used to answer until confirmed"
            )

        # A transcript that produced a memory which has since been SUPERSEDED
        # describes a past state of the world. The record stands - it is the
        # person's own words - but it must not be handed back as the current
        # answer, or the reversal we carefully tracked is undone at read time.
        source_id = p.get("interaction_id") or item.ref_id
        if item.kind != "memory" and source_id in superseded_sources:
            item.final -= settings.rag_p_superseded_source
            item.breakdown["superseded_source_penalty"] = settings.rag_p_superseded_source
            if item.final < settings.rag_support_threshold:
                item.included = False
                item.exclusion_reason = (
                    "source of a claim that was later reversed; superseded by a newer "
                    "statement"
                )

        out.append(item)

    out.sort(key=lambda f: -f.final)
    return out


def apply_threshold(items: list[Fused], threshold: float | None = None) -> list[Fused]:
    """
    Decide what actually counts as SUPPORT for the question (D-17).

    Two independent bars, and a candidate must clear both:

      1. relevance - it must resemble the question, either semantically above
         RAG_MIN_SIMILARITY or via a lexical match. A high-confidence memory
         about Acme's writing style is not evidence about Priya's manager, no
         matter how sure we are of it.
      2. score     - the fused score must clear RAG_SUPPORT_THRESHOLD.

    Everything that fails is kept in the trace with the reason, never dropped
    silently, because "why did memory not affect this?" is half the question.
    """
    threshold = settings.rag_support_threshold if threshold is None else threshold
    floor = settings.rag_min_similarity

    for item in items:
        if not item.included:
            continue
        relevant = item.best_similarity >= floor or item.lexical_hit
        if not relevant:
            item.included = False
            item.exclusion_reason = (
                f"not relevant to the question "
                f"(similarity {item.best_similarity:.3f} < {floor}, no lexical match)"
            )
        elif item.final < threshold:
            item.included = False
            item.exclusion_reason = (
                f"below support threshold ({item.final:.3f} < {threshold})"
            )
    return items
