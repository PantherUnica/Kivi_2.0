"""
Unit tests for the parts that must never drift: the fence, the confidence
formula, and the seven policy rules. No database, no model, no network.

    docker compose exec api python -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.memory import confidence as conf
from app.memory import sensitive
from app.memory.policy import (
    ASK,
    CONTRADICT,
    IGNORE,
    PROVISIONAL,
    REMEMBER,
    UPDATE,
    ExistingMemory,
    decide,
)

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def candidate(**kw) -> dict:
    base = {
        "type": "style_rule",
        "claim": "For Acme: avoids bullet points",
        "evidence_quote": "For Acme I never use bullet points.",
        "explicitness": "explicit_statement",
        "temporality": "durable",
        "scope": {"app": "Slack", "destination": "#acme", "project": "Halo"},
    }
    base.update(kw)
    return base


def existing(**kw) -> ExistingMemory:
    base = dict(
        id="m1", type="style_rule", claim="For Acme: avoids bullet points",
        scope={"app": "Slack", "destination": "#acme", "project": "Halo"},
        explicitness="explicit_statement", confidence=0.7, corroboration_count=1,
        status="active", created_at=NOW, similarity=0.0,
    )
    base.update(kw)
    return ExistingMemory(**base)


# ------------------------------------------------------------------ the fence
@pytest.mark.parametrize(
    "text,category",
    [
        ("Just left a doctor's appointment.", "health"),
        ("The salary discussion went badly.", "financial"),
        ("I had an argument with Dev and we are barely speaking.", "relationship_conflict"),
        ("Honestly I am feeling pretty anxious about it.", "emotional_state"),
    ],
)
def test_fence_catches_each_category(text, category):
    assert sensitive.classify(text) == category


def test_fence_leaves_ordinary_work_alone():
    assert sensitive.classify("The Halo spec is ready for review.") is None


def test_redaction_keeps_offsets_and_drops_only_the_fenced_sentence():
    text = "Just left a doctor's appointment. The Halo handoff is ready."
    safe, spans = sensitive.redact(text)
    assert len(spans) == 1 and spans[0]["category"] == "health"
    assert "doctor" not in safe
    assert "The Halo handoff is ready." in safe
    # offsets preserved, so evidence spans from surviving text stay accurate
    assert len(safe) == len(text)
    assert safe.index("The Halo") == text.index("The Halo")


def test_fenced_candidate_is_refused():
    out = decide(candidate(claim="Left a doctor's appointment early",
                           evidence_quote="I left a doctor's appointment early."),
                 [], now=NOW)
    assert out.decision == IGNORE
    assert out.rule_id == "R1_SENSITIVE_FENCE"


def test_explicit_remember_upgrades_a_fenced_span_to_a_question_not_a_memory():
    out = decide(candidate(claim="Left a doctor's appointment early",
                           evidence_quote="Remember this: my doctor's appointment is Friday."),
                 [], now=NOW)
    assert out.decision == ASK
    assert out.status == "provisional"


# ------------------------------------------------------------- the seven rules
def test_ephemeral_is_ignored():
    out = decide(candidate(temporality="ephemeral",
                           claim="Use the blue slides for tomorrow"), [], now=NOW)
    assert out.decision == IGNORE and out.rule_id == "R2_EPHEMERAL"


def test_inference_never_becomes_active_on_its_own():
    out = decide(candidate(explicitness="inferred",
                           claim="Probably more productive in the mornings"), [], now=NOW)
    assert out.decision == PROVISIONAL
    assert out.rule_id == "R3_INFERENCE_FLOOR"
    assert out.status == "provisional"      # the load-bearing assertion


def test_restatement_corroborates_rather_than_duplicating():
    out = decide(candidate(), [existing(similarity=0.93)], now=NOW)
    assert out.decision == UPDATE
    assert out.rule_id == "R4_DUPLICATE"
    assert out.target_memory_id == "m1"
    assert out.extras["corroboration_count"] == 2
    assert out.computed_confidence > 0.7      # confidence rises with corroboration


def test_explicit_reversal_supersedes_rather_than_deleting():
    out = decide(
        candidate(type="decision", claim="We are moving Halo off Postgres to DynamoDB",
                  evidence_quote="We are moving Halo off Postgres to DynamoDB."),
        [existing(type="decision", claim="We are going with Postgres for Halo",
                  similarity=0.80)],
        contradiction_check=lambda a, b: True,
        now=NOW,
    )
    assert out.decision == CONTRADICT
    assert out.rule_id == "R5_CONTRADICTION"
    assert out.extras["supersedes"] == "m1"


def test_a_hedged_conflict_asks_instead_of_overruling():
    out = decide(
        candidate(type="decision", explicitness="inferred",
                  claim="Maybe we should move Halo to DynamoDB"),
        [existing(type="decision", claim="We are going with Postgres for Halo",
                  similarity=0.80)],
        contradiction_check=lambda a, b: True,
        now=NOW,
    )
    assert out.decision == ASK
    assert out.rule_id == "R5_CONTRADICTION"


def test_the_same_rule_in_another_context_does_not_widen_the_old_one():
    other_scope = existing(
        scope={"app": "Gmail", "destination": "rohan@northwind.com", "project": "Atlas"},
        similarity=0.95,
    )
    out = decide(candidate(), [other_scope], now=NOW)
    assert out.decision == REMEMBER
    assert out.rule_id == "R6_SCOPE_SPLIT"
    assert out.target_memory_id is None       # a NEW scoped memory, not an edit


def test_a_new_durable_statement_is_remembered():
    out = decide(candidate(), [], now=NOW)
    assert out.decision == REMEMBER and out.rule_id == "R7_REMEMBER"
    assert out.status == "active"


# --------------------------------------------------------------- confidence
def test_confidence_ranks_provenance_correctly():
    explicit = conf.compute("explicit_statement", 1)
    repeated = conf.compute("observed_repetition", 1)
    inferred = conf.compute("inferred", 1)
    assert inferred < repeated < explicit


def test_confidence_rises_with_corroboration_but_is_capped():
    assert conf.compute("explicit_statement", 5) > conf.compute("explicit_statement", 1)
    assert conf.compute("explicit_statement", 50) <= 0.98


def test_confidence_is_never_certain():
    for grade in ("explicit_statement", "user_correction", "observed_repetition", "inferred"):
        assert conf.compute(grade, 99, user_confirmed=True) < 1.0


def test_commitments_go_stale_faster_than_style():
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    commitment = conf.compute("explicit_statement", 1, "commitment", last_confirmed_at=old, now=NOW)
    style = conf.compute("explicit_statement", 1, "style_rule", last_confirmed_at=old, now=NOW)
    assert commitment < style
