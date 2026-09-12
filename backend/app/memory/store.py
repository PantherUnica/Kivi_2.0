"""
Memory persistence and the ingest pipeline.

    interaction -> redact fence -> extract candidates -> policy -> commit
                         |                                  |
                    ignored_spans                    memory_decisions

Every path writes an audit row. A candidate that is rejected leaves exactly as
much evidence behind as one that is kept.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session

from app.config import settings
from app.memory import confidence as conf
from app.memory import sensitive
from app.memory.extractor import extract, make_contradiction_checker
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
from app.models import (
    Episode,
    IgnoredSpan,
    Interaction,
    Memory,
    MemoryDecision,
    MemoryEvent,
    MemoryEvidence,
)
from app.providers import get_embedder

log = logging.getLogger(__name__)


def _cos(a, b) -> float:
    """
    Cosine similarity, tolerant of what pgvector actually hands back.

    Stored embeddings come out of the driver as numpy arrays, not lists, so a
    plain truthiness check on them raises. Compare lengths instead.
    """
    if a is None or b is None:
        return 0.0
    va = np.asarray(a, dtype=np.float32).ravel()
    vb = np.asarray(b, dtype=np.float32).ravel()
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def log_event(db: Session, memory_id: str, event_type: str, *, actor: str = "system",
              before: dict | None = None, after: dict | None = None,
              note: str | None = None) -> None:
    db.add(
        MemoryEvent(
            memory_id=memory_id, event_type=event_type, actor=actor,
            before=before, after=after, note=note,
        )
    )


def memory_snapshot(m: Memory) -> dict:
    return {
        "claim": m.claim, "status": m.status, "confidence": m.confidence,
        "corroboration_count": m.corroboration_count, "scope": m.scope,
        "explicitness": m.explicitness,
    }


def _neighbours(db: Session, user_id: str, ctype: str, vec: list[float]) -> list[ExistingMemory]:
    """Same-type memories, scored for cosine against the candidate."""
    rows = db.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.type == ctype,
            Memory.status.in_(("active", "provisional")),
        )
    ).scalars().all()
    out = []
    for m in rows:
        out.append(
            ExistingMemory(
                id=m.id, type=m.type, claim=m.claim, scope=m.scope or {},
                explicitness=m.explicitness, confidence=m.confidence,
                corroboration_count=m.corroboration_count, status=m.status,
                created_at=m.created_at, similarity=_cos(vec, m.embedding),
            )
        )
    return out


def _due_date(claim: str, captured_at: datetime) -> datetime | None:
    """Resolve a stated weekday/EOD into a real expiry for commitments."""
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    low = claim.lower()
    for i, day in enumerate(days):
        if day in low:
            ahead = (i - captured_at.weekday()) % 7 or 7
            return captured_at + timedelta(days=ahead)
    if "end of week" in low or "eow" in low:
        return captured_at + timedelta(days=(4 - captured_at.weekday()) % 7 or 7)
    if "eod" in low or "end of day" in low or "today" in low:
        return captured_at + timedelta(days=1)
    return captured_at + timedelta(days=30)


def process_interaction(db: Session, interaction: Interaction) -> dict:
    """
    Run one interaction through the full memory pipeline.

    Returns a summary the UI uses for the quiet "Kivi learned..." note (D-19).
    """
    embedder = get_embedder()
    checker = make_contradiction_checker()
    summary = {
        "interaction_id": interaction.id,
        "created": [], "updated": [], "ignored": [], "asked": [], "provisional": [],
        "fenced": [], "tokens": 0, "cost_usd": 0.0, "latency_ms": 0,
    }

    source = interaction.formatted_text or interaction.raw_asr

    # ---- the fence, applied BEFORE the model sees anything (D-06) ---------
    redacted, fenced = sensitive.redact(source)
    for span in fenced:
        if span["explicit_remember"]:
            continue
        db.add(
            IgnoredSpan(
                user_id=interaction.user_id,
                interaction_id=interaction.id,
                category=span["category"],
                reason=(
                    f"Fenced category '{span['category']}'. Removed before extraction; "
                    f"no memory may be derived from it."
                ),
                rule_id="R1_SENSITIVE_FENCE",
                span_hash=span["span_hash"],
                occurred_at=interaction.captured_at,
            )
        )
        summary["fenced"].append(span["category"])

    if not redacted.strip():
        return summary

    # ---- propose ---------------------------------------------------------
    context = {
        "app": interaction.app,
        "destination": interaction.destination,
        "project_hint": interaction.project_hint,
        # The client is the audience a person actually names ("write it for
        # Acme"), while the destination is usually a channel. Both are scope.
        "client": (interaction.meta or {}).get("client"),
        "captured_at": interaction.captured_at.isoformat(),
    }
    candidates, rejected, llm_res = extract(redacted, context)
    summary["tokens"] = llm_res.prompt_tokens + llm_res.completion_tokens
    summary["cost_usd"] = llm_res.cost_usd
    summary["latency_ms"] = llm_res.latency_ms

    for bad in rejected:
        db.add(
            MemoryDecision(
                user_id=interaction.user_id, interaction_id=interaction.id,
                candidate=bad["candidate"], decision=IGNORE,
                rule_id="R0_MALFORMED", rationale=bad["reason"],
                model=llm_res.model, prompt_tokens=llm_res.prompt_tokens,
                completion_tokens=llm_res.completion_tokens,
                latency_ms=llm_res.latency_ms, cost_usd=llm_res.cost_usd,
            )
        )

    if not candidates:
        return summary

    vectors = embedder.embed([c["claim"] for c in candidates])

    # ---- decide ----------------------------------------------------------
    for candidate, vec in zip(candidates, vectors, strict=False):
        neighbours = _neighbours(db, interaction.user_id, candidate["type"], vec)
        outcome = decide(candidate, neighbours, contradiction_check=checker,
                         now=interaction.captured_at)

        resulting_id = None

        # ASK stores a PROVISIONAL memory rather than nothing. Retrieval will
        # not use it (fusion excludes provisional), but it now has an id, so
        # the person can actually answer the question Kivi is asking.
        if outcome.decision in (REMEMBER, PROVISIONAL, CONTRADICT, ASK):
            mem = Memory(
                user_id=interaction.user_id,
                type=candidate["type"],
                claim=candidate["claim"],
                scope=candidate.get("scope") or {},
                explicitness=candidate.get("explicitness", "observed_repetition"),
                confidence=outcome.computed_confidence,
                corroboration_count=1,
                status=outcome.status,
                sensitivity=outcome.extras.get("sensitive_category", "none"),
                action_policy="style_only" if candidate["type"] == "style_rule"
                else "answer_use",
                created_at=interaction.captured_at,
                last_confirmed_at=interaction.captured_at,
                valid_from=interaction.captured_at,
                embedding=vec,
            )
            if candidate["type"] == "commitment":
                mem.expires_at = _due_date(candidate["claim"], interaction.captured_at)

            db.add(mem)
            db.flush()          # mem.id is generated on INSERT, so flush first
            resulting_id = mem.id

            if outcome.decision == CONTRADICT and outcome.target_memory_id:
                old = db.get(Memory, outcome.target_memory_id)
                if old is not None:
                    before = memory_snapshot(old)
                    old.status = "superseded"
                    old.superseded_by_id = mem.id
                    mem.supersedes_id = old.id
                    log_event(db, old.id, "superseded", before=before,
                              after=memory_snapshot(old), note=outcome.rationale)
            db.add(
                MemoryEvidence(
                    memory_id=mem.id, interaction_id=interaction.id,
                    quote=candidate["evidence_quote"],
                    char_start=candidate.get("char_start", 0),
                    char_end=candidate.get("char_end", 0),
                    captured_at=interaction.captured_at,
                )
            )
            log_event(db, mem.id, "created", after=memory_snapshot(mem),
                      note=f"{outcome.rule_id}: {outcome.rationale}")
            if outcome.decision == ASK:
                summary["asked"].append(
                    {"id": mem.id, "claim": mem.claim, "why": outcome.rationale,
                     "type": mem.type}
                )
            else:
                bucket = "provisional" if outcome.status == "provisional" else "created"
                summary[bucket].append(
                    {"id": mem.id, "type": mem.type, "claim": mem.claim}
                )

        elif outcome.decision == UPDATE and outcome.target_memory_id:
            mem = db.get(Memory, outcome.target_memory_id)
            if mem is not None:
                before = memory_snapshot(mem)
                mem.corroboration_count = outcome.extras.get(
                    "corroboration_count", mem.corroboration_count + 1
                )
                mem.confidence = outcome.computed_confidence
                mem.last_confirmed_at = interaction.captured_at
                mem.status = outcome.status
                db.add(
                    MemoryEvidence(
                        memory_id=mem.id, interaction_id=interaction.id,
                        quote=candidate["evidence_quote"],
                        char_start=candidate.get("char_start", 0),
                        char_end=candidate.get("char_end", 0),
                        captured_at=interaction.captured_at,
                    )
                )
                log_event(db, mem.id, "corroborated", before=before,
                          after=memory_snapshot(mem), note=outcome.rationale)
                resulting_id = mem.id
                summary["updated"].append(
                    {"id": mem.id, "type": mem.type, "claim": mem.claim,
                     "corroborations": mem.corroboration_count}
                )

        else:
            summary["ignored"].append(
                {"claim": candidate.get("claim", ""), "rule": outcome.rule_id,
                 "why": outcome.rationale}
            )

        db.add(
            MemoryDecision(
                user_id=interaction.user_id, interaction_id=interaction.id,
                candidate=candidate, decision=outcome.decision,
                rule_id=outcome.rule_id, rationale=outcome.rationale,
                resulting_memory_id=resulting_id, model=llm_res.model,
                prompt_tokens=llm_res.prompt_tokens,
                completion_tokens=llm_res.completion_tokens,
                latency_ms=llm_res.latency_ms, cost_usd=llm_res.cost_usd,
            )
        )

    return summary


def correct_memory(db: Session, memory: Memory, new_claim: str,
                   note: str = "corrected by user") -> Memory:
    """
    Supersede a memory with a user-authored correction (D-05, D-15).

    The old row is kept and linked, so "what did Kivi used to think?" stays
    answerable and the correction is provable.
    """
    embedder = get_embedder()
    before = memory_snapshot(memory)

    replacement = Memory(
        user_id=memory.user_id,
        type=memory.type,
        claim=new_claim,
        scope=memory.scope,
        explicitness="user_correction",
        confidence=conf.compute("user_correction", 1, memory.type,
                                last_confirmed_at=datetime.now(timezone.utc)),
        corroboration_count=1,
        user_confirmed=True,
        status="active",
        action_policy=memory.action_policy,
        last_confirmed_at=datetime.now(timezone.utc),
        valid_from=datetime.now(timezone.utc),
        supersedes_id=memory.id,
        embedding=embedder.embed([new_claim])[0],
    )
    db.add(replacement)
    db.flush()

    memory.status = "superseded"
    memory.superseded_by_id = replacement.id

    log_event(db, memory.id, "superseded", actor="user", before=before,
              after=memory_snapshot(memory), note=note)
    log_event(db, replacement.id, "corrected", actor="user",
              after=memory_snapshot(replacement), note=note)

    # Carry the evidence forward so the correction is not left unsupported.
    for ev in memory.evidence:
        db.add(
            MemoryEvidence(
                memory_id=replacement.id, interaction_id=ev.interaction_id,
                quote=ev.quote, char_start=ev.char_start, char_end=ev.char_end,
                captured_at=ev.captured_at, weight=0.5,
            )
        )
    return replacement


def forget_memory(db: Session, memory: Memory, note: str = "forgotten by user") -> None:
    """
    Deactivate a memory (D-15).

    Not a hard delete: the claim text is cleared, the embedding is nulled so no
    vector search can surface it, and the row remains as proof the forget
    happened and when.
    """
    before = memory_snapshot(memory)
    memory.status = "forgotten"
    memory.forgotten_at = datetime.now(timezone.utc)
    memory.embedding = None
    log_event(db, memory.id, "forgotten", actor="user", before=before,
              after=memory_snapshot(memory), note=note)


def confirm_memory(db: Session, memory: Memory) -> None:
    """Promote a provisional memory because the user answered the question."""
    before = memory_snapshot(memory)
    memory.user_confirmed = True
    memory.status = "active"
    memory.last_confirmed_at = datetime.now(timezone.utc)
    memory.confidence = conf.compute(
        memory.explicitness, memory.corroboration_count, memory.type,
        last_confirmed_at=memory.last_confirmed_at, user_confirmed=True,
    )
    log_event(db, memory.id, "confirmed", actor="user", before=before,
              after=memory_snapshot(memory), note="user confirmed")


def expire_due_commitments(db: Session, user_id: str, now: datetime | None = None) -> int:
    """Commitments past their due date stop being live memory."""
    now = now or datetime.now(timezone.utc)
    rows = db.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.type == "commitment",
            Memory.status == "active",
            Memory.expires_at.is_not(None),
            Memory.expires_at < now,
        )
    ).scalars().all()
    for m in rows:
        before = memory_snapshot(m)
        m.status = "expired"
        log_event(db, m.id, "expired", before=before, after=memory_snapshot(m),
                  note=f"due {m.expires_at.isoformat()} has passed")
    return len(rows)


def snapshot_growth(db: Session, label: str) -> list[dict]:
    """Record row counts and on-disk size per table, for the growth metric."""
    from app.models import DbGrowthSnapshot

    tables = [
        "interactions", "interaction_chunks", "episodes", "memories",
        "memory_evidence", "memory_decisions", "memory_events", "ignored_spans",
        "hey_kivi_turns", "retrieval_traces",
    ]
    out = []
    for t in tables:
        count = db.execute(sql_text(f"SELECT count(*) FROM {t}")).scalar() or 0
        size = db.execute(
            sql_text("SELECT pg_total_relation_size(:t)"), {"t": t}
        ).scalar() or 0
        db.add(DbGrowthSnapshot(label=label, table_name=t, row_count=count,
                                total_bytes=size))
        out.append({"table": t, "rows": count, "bytes": size})
    return out
