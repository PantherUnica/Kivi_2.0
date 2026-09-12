"""
Confidence (DECISIONS.md D-14).

A published formula, not a model's self-report, and never 1.0.

    conf = base(explicitness)
         + 0.10 * min(corroborations, 2.5)
         - decay(type, age)
         + 0.15 * user_confirmed
    clamped to [0.05, 0.98]

Lee & See (2004): the goal is appropriate reliance, not maximum trust.
A system that reports certainty invites automation bias.
"""
from __future__ import annotations

from datetime import datetime, timezone

BASE = {
    "user_correction": 0.95,
    "explicit_statement": 0.70,
    "observed_repetition": 0.50,
    "inferred": 0.30,
}

# Half-life in days. A style rule ages slowly; a commitment goes stale fast.
HALF_LIFE_DAYS = {
    "style_rule": 540.0,
    "project_context": 365.0,
    "routine": 240.0,
    "decision": 180.0,
    "commitment": 21.0,
}

MAX_DECAY = 0.25


def decay(memory_type: str, age_days: float) -> float:
    half_life = HALF_LIFE_DAYS.get(memory_type, 240.0)
    if age_days <= 0:
        return 0.0
    return min(MAX_DECAY, MAX_DECAY * (1 - 0.5 ** (age_days / half_life)))


def compute(
    explicitness: str,
    corroborations: int = 1,
    memory_type: str = "style_rule",
    last_confirmed_at: datetime | None = None,
    user_confirmed: bool = False,
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(timezone.utc)
    score = BASE.get(explicitness, 0.4)
    score += 0.10 * min(max(corroborations - 1, 0), 2.5)

    if last_confirmed_at is not None:
        if last_confirmed_at.tzinfo is None:
            last_confirmed_at = last_confirmed_at.replace(tzinfo=timezone.utc)
        age_days = (now - last_confirmed_at).total_seconds() / 86400.0
        score -= decay(memory_type, age_days)

    if user_confirmed:
        score += 0.15

    return round(max(0.05, min(0.98, score)), 3)


def recency_weight(memory_type: str, last_confirmed_at: datetime | None,
                   now: datetime | None = None) -> float:
    """0..1 freshness signal used by the RAG reranker (D-16)."""
    if last_confirmed_at is None:
        return 0.5
    now = now or datetime.now(timezone.utc)
    if last_confirmed_at.tzinfo is None:
        last_confirmed_at = last_confirmed_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - last_confirmed_at).total_seconds() / 86400.0)
    half_life = HALF_LIFE_DAYS.get(memory_type, 240.0)
    return round(0.5 ** (age_days / half_life), 4)
