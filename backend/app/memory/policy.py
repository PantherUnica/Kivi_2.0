"""
The memory policy engine (DECISIONS.md D-13).

The LLM proposes candidates. THIS decides. Seven ordered rules, plain Python,
each with a stable id that appears in every trace row and in the UI.

Why not let the model decide: "why did Kivi remember this?" must have an
answer that does not change with temperature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.memory import confidence as conf
from app.memory import sensitive

REMEMBER = "REMEMBER"
UPDATE = "UPDATE"
IGNORE = "IGNORE"
CONTRADICT = "CONTRADICT"
ASK = "ASK_CONFIRMATION"
PROVISIONAL = "PROVISIONAL"


@dataclass
class ExistingMemory:
    """The subset of a stored memory the policy needs. Keeps policy DB-free."""

    id: str
    type: str
    claim: str
    scope: dict
    explicitness: str
    confidence: float
    corroboration_count: int
    status: str
    created_at: datetime
    similarity: float = 0.0


@dataclass
class PolicyOutcome:
    decision: str
    rule_id: str
    rationale: str
    target_memory_id: str | None = None
    computed_confidence: float = 0.0
    status: str = "active"
    extras: dict[str, Any] = field(default_factory=dict)


def _scope_key(scope: dict | None) -> tuple:
    scope = scope or {}
    return (
        (scope.get("destination") or "").lower(),
        (scope.get("app") or "").lower(),
        (scope.get("project") or "").lower(),
    )


def _same_scope(a: dict | None, b: dict | None) -> bool:
    return _scope_key(a) == _scope_key(b)


def decide(
    candidate: dict,
    neighbours: list[ExistingMemory],
    *,
    contradiction_check=None,
    now: datetime | None = None,
) -> PolicyOutcome:
    """
    Apply the rules in order. First match wins.

    `neighbours` are existing memories of the same type, already scored for
    cosine similarity against the candidate. `contradiction_check` is an
    optional callable(claim_a, claim_b) -> bool, backed by the LLM.
    """
    now = now or datetime.now(timezone.utc)
    ctype = candidate.get("type", "")
    claim = candidate.get("claim", "")
    scope = candidate.get("scope") or {}
    explicitness = candidate.get("explicitness", "observed_repetition")
    temporality = candidate.get("temporality", "durable")

    # ---------------------------------------------------------------- R1
    # The fence. Checked here as well as at redaction time, because a candidate
    # can be assembled from surviving fragments.
    category = sensitive.classify(claim) or sensitive.classify(
        candidate.get("evidence_quote", "")
    )
    if category:
        if candidate.get("explicit_remember") or sensitive.EXPLICIT_REMEMBER.search(
            candidate.get("evidence_quote", "")
        ):
            return PolicyOutcome(
                decision=ASK,
                rule_id="R1_SENSITIVE_FENCE",
                rationale=(
                    f"Touches the fenced category '{category}'. You asked me to remember "
                    f"it explicitly, so I will only keep it if you confirm."
                ),
                status="provisional",
                computed_confidence=0.0,
                extras={"sensitive_category": category},
            )
        return PolicyOutcome(
            decision=IGNORE,
            rule_id="R1_SENSITIVE_FENCE",
            rationale=(
                f"Fenced category '{category}'. Kivi never derives memory from "
                f"health, financial, relationship-conflict or emotional-state material."
            ),
            extras={"sensitive_category": category},
        )

    # ---------------------------------------------------------------- R2
    if temporality == "ephemeral":
        return PolicyOutcome(
            decision=IGNORE,
            rule_id="R2_EPHEMERAL",
            rationale="Describes a one-off or today-only state, not a durable pattern.",
        )

    # ---------------------------------------------------------------- R4
    # Duplicate before contradiction: an identical restatement is corroboration,
    # not a conflict, and checking it first saves a model call.
    dupes = [
        n for n in neighbours
        if n.similarity >= settings.policy_duplicate_cosine
        and n.type == ctype
        and _same_scope(n.scope, scope)
        and n.status in ("active", "provisional")
    ]
    if dupes:
        target = max(dupes, key=lambda n: n.similarity)
        corroborations = target.corroboration_count + 1
        promoted = (
            explicitness != "inferred"
            or corroborations >= settings.policy_promote_after_corroborations
        )
        new_conf = conf.compute(
            explicitness if explicitness != "inferred" else target.explicitness,
            corroborations=corroborations,
            memory_type=ctype,
            last_confirmed_at=now,
            now=now,
        )
        return PolicyOutcome(
            decision=UPDATE,
            rule_id="R4_DUPLICATE",
            rationale=(
                f"Restates an existing memory (cosine {target.similarity:.2f}). "
                f"Corroboration {target.corroboration_count} to {corroborations}; "
                f"confidence {target.confidence:.2f} to {new_conf:.2f}."
            ),
            target_memory_id=target.id,
            computed_confidence=new_conf,
            status="active" if promoted else "provisional",
            extras={"corroboration_count": corroborations},
        )

    # ---------------------------------------------------------------- R5
    # Same type, same scope, related enough to be about the same thing, but not
    # a restatement. Ask the model whether it is actually opposed.
    rivals = [
        n for n in neighbours
        if n.type == ctype
        and _same_scope(n.scope, scope)
        and settings.policy_contradiction_cosine <= n.similarity < settings.policy_duplicate_cosine
        and n.status == "active"
    ]
    for rival in sorted(rivals, key=lambda n: -n.similarity):
        opposed = True
        if contradiction_check is not None:
            opposed = bool(contradiction_check(rival.claim, claim))
        if not opposed:
            continue

        newer = True
        explicit_enough = explicitness in ("explicit_statement", "user_correction")
        if newer and explicit_enough:
            new_conf = conf.compute(explicitness, 1, ctype, last_confirmed_at=now, now=now)
            return PolicyOutcome(
                decision=CONTRADICT,
                rule_id="R5_CONTRADICTION",
                rationale=(
                    f"Contradicts an existing memory in the same scope "
                    f"(cosine {rival.similarity:.2f}). The new statement is explicit and "
                    f"more recent, so the old one is superseded rather than deleted."
                ),
                target_memory_id=rival.id,
                computed_confidence=new_conf,
                status="active",
                extras={"supersedes": rival.id, "old_claim": rival.claim},
            )
        return PolicyOutcome(
            decision=ASK,
            rule_id="R5_CONTRADICTION",
            rationale=(
                f"Conflicts with something you told me before, but this mention is not "
                f"explicit enough to overrule it on its own. Offered back as a question."
            ),
            target_memory_id=rival.id,
            status="provisional",
            computed_confidence=conf.compute(explicitness, 1, ctype, last_confirmed_at=now, now=now),
            extras={"conflicts_with": rival.id, "old_claim": rival.claim},
        )

    # ---------------------------------------------------------------- R3
    # Inference floor. An inference can be known; it cannot become a fact on
    # its own. It is stored as `provisional`, which retrieval will not use.
    if explicitness == "inferred":
        return PolicyOutcome(
            decision=PROVISIONAL,
            rule_id="R3_INFERENCE_FLOOR",
            rationale=(
                "Read as an inference ('I think', 'probably', 'maybe') rather than a "
                "statement. Held as provisional and never used to answer until you "
                "confirm it."
            ),
            computed_confidence=conf.compute("inferred", 1, ctype, last_confirmed_at=now, now=now),
            status="provisional",
        )

    # ---------------------------------------------------------------- R6
    # The same claim in a DIFFERENT scope is a new scoped memory, never a
    # widening of the old one. Preferences do not globalise.
    cross_scope = [
        n for n in neighbours
        if n.type == ctype
        and n.similarity >= settings.policy_duplicate_cosine
        and not _same_scope(n.scope, scope)
        and n.status == "active"
    ]
    if cross_scope:
        other = cross_scope[0]
        return PolicyOutcome(
            decision=REMEMBER,
            rule_id="R6_SCOPE_SPLIT",
            rationale=(
                f"Same pattern as an existing memory but in a different context "
                f"({_scope_key(other.scope)} vs {_scope_key(scope)}). Stored as its own "
                f"scoped memory; a preference learned in one place is not widened to all."
            ),
            computed_confidence=conf.compute(
                explicitness, 1, ctype, last_confirmed_at=now, now=now
            ),
            status="active",
            extras={"sibling_scope_of": other.id},
        )

    # ---------------------------------------------------------------- R7
    new_conf = conf.compute(explicitness, 1, ctype, last_confirmed_at=now, now=now)
    return PolicyOutcome(
        decision=REMEMBER,
        rule_id="R7_REMEMBER",
        rationale=(
            f"New durable {ctype.replace('_', ' ')} stated "
            f"{'explicitly' if explicitness == 'explicit_statement' else 'in passing'}; "
            f"no existing memory covers it in this scope."
        ),
        computed_confidence=new_conf,
        status="active",
    )
