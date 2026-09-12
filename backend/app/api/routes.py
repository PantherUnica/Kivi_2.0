"""HTTP surface. Thin: all behaviour lives in the modules below it."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api import orchestrator
from app.config import settings
from app.db import get_db
from app.ingest.importer import get_or_create_user, import_corpus
from app.memory.store import confirm_memory, process_interaction
from app.models import (
    HeyKiviTurn,
    IgnoredSpan,
    Interaction,
    Memory,
    MemoryDecision,
    MemoryEvent,
    RetrievalTrace,
    User,
)
from app.providers import get_embedder, get_llm
from app.tools import registry

router = APIRouter()


def _user(db: Session) -> User:
    return get_or_create_user(db, settings.kivi_user_handle)


# ------------------------------------------------------------------ health
@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    embedder = get_embedder()
    counts = {
        t.__tablename__: db.execute(select(func.count()).select_from(t)).scalar()
        for t in (Interaction, Memory, IgnoredSpan, HeyKiviTurn)
    }
    return {
        "ok": True,
        "llm_provider": settings.llm_provider,
        "llm_model": getattr(get_llm(), "model", "mock-deterministic"),
        "embedding_provider": getattr(embedder, "name", "unknown"),
        "embedding_degraded": bool(getattr(embedder, "degraded", False)),
        "embed_dim": settings.embed_dim,
        "counts": counts,
    }


# ------------------------------------------------------------------ hey kivi
class AskRequest(BaseModel):
    utterance: str
    session_id: str = "default"
    destination: str | None = None
    app: str | None = None
    text: str | None = None
    now: datetime | None = None


@router.post("/hey-kivi/ask")
def ask(req: AskRequest, db: Session = Depends(get_db)) -> dict:
    if not req.utterance.strip():
        raise HTTPException(400, "utterance is required")
    return orchestrator.handle(
        db, _user(db).id, req.utterance, session_id=req.session_id,
        destination=req.destination, app=req.app, text=req.text, now=req.now,
    )


# ------------------------------------------------------------------ dictation
class DictateRequest(BaseModel):
    text: str
    raw_asr: str | None = None
    app: str | None = None
    destination: str | None = None
    project: str | None = None
    captured_at: datetime | None = None


@router.post("/dictation")
def dictate(req: DictateRequest, db: Session = Depends(get_db)) -> dict:
    """
    Capture a dictation.

    Memory never rewrites the text (D-19). The only memory-side output is the
    quiet "learned" summary the client shows as a dismissible note.
    """
    from app.ingest.chunker import chunk
    from app.models import Episode, InteractionChunk

    user = _user(db)
    when = req.captured_at or datetime.now(timezone.utc)
    interaction = Interaction(
        user_id=user.id, captured_at=when, mode="dictation",
        app=req.app, destination=req.destination, project_hint=req.project,
        raw_asr=req.raw_asr or req.text, formatted_text=req.text,
        source_corpus="live",
    )
    db.add(interaction)
    db.flush()

    from app.memory import sensitive

    safe, _ = sensitive.redact(req.text)
    interaction.safe_text = safe

    embedder = get_embedder()
    pieces = chunk(safe)
    if pieces:
        vecs = embedder.embed([p["text"] for p in pieces])
        for piece, vec in zip(pieces, vecs, strict=False):
            db.add(InteractionChunk(interaction_id=interaction.id, embedding=vec, **piece))

    title = safe.strip().split(".")[0][:120] or "Dictation"
    db.add(
        Episode(
            user_id=user.id, interaction_id=interaction.id, title=title,
            summary=safe[:600], occurred_at=when, app=req.app,
            destination=req.destination,
            embedding=embedder.embed([f"{title}. {safe[:600]}"])[0],
        )
    )

    learned = process_interaction(db, interaction)
    db.commit()

    return {
        "interaction_id": interaction.id,
        "text": req.text,            # unchanged. always.
        "learned": learned,
        "note": _learned_note(learned),
    }


def _learned_note(learned: dict) -> str | None:
    bits = []
    if learned["created"]:
        bits.append(f"learned {len(learned['created'])} new thing(s)")
    if learned["updated"]:
        bits.append(f"confirmed {len(learned['updated'])}")
    if learned["fenced"]:
        bits.append(f"chose not to remember {len(learned['fenced'])}")
    return "Kivi " + ", ".join(bits) + "." if bits else None


# ------------------------------------------------------------------ memory
@router.get("/memories")
def list_memories(status: str | None = None, type: str | None = None,
                  limit: int = 200, db: Session = Depends(get_db)) -> dict:
    user = _user(db)
    stmt = select(Memory).where(Memory.user_id == user.id)
    if status:
        stmt = stmt.where(Memory.status == status)
    else:
        stmt = stmt.where(Memory.status.in_(("active", "provisional")))
    if type:
        stmt = stmt.where(Memory.type == type)
    rows = db.execute(
        stmt.order_by(Memory.type, desc(Memory.confidence)).limit(limit)
    ).scalars().all()
    return {"memories": [registry.memory_dto(db, m) for m in rows], "count": len(rows)}


@router.get("/memories/{memory_id}")
def get_memory(memory_id: str, db: Session = Depends(get_db)) -> dict:
    mem = db.get(Memory, memory_id)
    if mem is None:
        raise HTTPException(404, "no such memory")
    events = db.execute(
        select(MemoryEvent).where(MemoryEvent.memory_id == memory_id)
        .order_by(MemoryEvent.created_at)
    ).scalars().all()
    decisions = db.execute(
        select(MemoryDecision).where(MemoryDecision.resulting_memory_id == memory_id)
        .order_by(MemoryDecision.created_at)
    ).scalars().all()
    return {
        **registry.memory_dto(db, mem),
        "events": [
            {"type": e.event_type, "actor": e.actor, "at": e.created_at.isoformat(),
             "note": e.note, "before": e.before, "after": e.after}
            for e in events
        ],
        "decisions": [
            {"decision": d.decision, "rule_id": d.rule_id, "rationale": d.rationale,
             "at": d.created_at.isoformat(), "candidate": d.candidate,
             "model": d.model, "latency_ms": d.latency_ms, "cost_usd": d.cost_usd}
            for d in decisions
        ],
    }


class ConfirmRequest(BaseModel):
    confirm: bool = True


@router.post("/memories/{memory_id}/confirm")
def confirm(memory_id: str, req: ConfirmRequest, db: Session = Depends(get_db)) -> dict:
    """Promote a provisional memory because the person answered the question."""
    from app.memory.store import forget_memory

    mem = db.get(Memory, memory_id)
    if mem is None:
        raise HTTPException(404, "no such memory")
    if req.confirm:
        confirm_memory(db, mem)
    else:
        forget_memory(db, mem, note="user declined at confirmation")
    db.commit()
    return registry.memory_dto(db, mem)


class MemoryTextRequest(BaseModel):
    description: str
    new_claim: str | None = None


@router.post("/memories/correct")
def correct_memory_route(req: MemoryTextRequest, db: Session = Depends(get_db)) -> dict:
    return registry.correct(db, _user(db).id, req.description,
                            req.new_claim or req.description)


@router.post("/memories/forget")
def forget_memory_route(req: MemoryTextRequest, db: Session = Depends(get_db)) -> dict:
    return registry.forget(db, _user(db).id, req.description)


@router.get("/what-kivi-knows")
def what_kivi_knows(topic: str = "", db: Session = Depends(get_db)) -> dict:
    return registry.what_do_you_know(db, _user(db).id, topic)


# ---------------------------------------------------------------- inspection
@router.get("/interactions")
def list_interactions(limit: int = 50, offset: int = 0,
                      db: Session = Depends(get_db)) -> dict:
    user = _user(db)
    rows = db.execute(
        select(Interaction).where(Interaction.user_id == user.id)
        .order_by(desc(Interaction.captured_at)).limit(limit).offset(offset)
    ).scalars().all()
    total = db.execute(
        select(func.count()).select_from(Interaction).where(Interaction.user_id == user.id)
    ).scalar()
    return {
        "total": total,
        "interactions": [
            {"id": i.id, "captured_at": i.captured_at.isoformat(), "app": i.app,
             "destination": i.destination, "project": i.project_hint,
             "text": i.formatted_text, "raw_asr": i.raw_asr,
             "style_used": i.style_used, "external_id": i.external_id}
            for i in rows
        ],
    }


@router.get("/interactions/{interaction_id}")
def get_interaction(interaction_id: str, db: Session = Depends(get_db)) -> dict:
    inter = db.get(Interaction, interaction_id)
    if inter is None:
        raise HTTPException(404, "no such interaction")
    decisions = db.execute(
        select(MemoryDecision).where(MemoryDecision.interaction_id == interaction_id)
    ).scalars().all()
    ignored = db.execute(
        select(IgnoredSpan).where(IgnoredSpan.interaction_id == interaction_id)
    ).scalars().all()
    return {
        "id": inter.id,
        "captured_at": inter.captured_at.isoformat(),
        "app": inter.app, "destination": inter.destination,
        "project": inter.project_hint,
        "raw_asr": inter.raw_asr, "formatted_text": inter.formatted_text,
        "meta": inter.meta,
        "decisions": [
            {"decision": d.decision, "rule_id": d.rule_id, "rationale": d.rationale,
             "candidate": d.candidate, "resulting_memory_id": d.resulting_memory_id}
            for d in decisions
        ],
        "ignored_spans": [
            {"category": s.category, "reason": s.reason, "rule_id": s.rule_id}
            for s in ignored
        ],
    }


@router.get("/turns/{turn_id}/trace")
def turn_trace(turn_id: str, db: Session = Depends(get_db)) -> dict:
    """'Why did Kivi say this?' - the engineer-grade view behind any answer."""
    turn = db.get(HeyKiviTurn, turn_id)
    if turn is None:
        raise HTTPException(404, "no such turn")
    traces = db.execute(
        select(RetrievalTrace).where(RetrievalTrace.turn_id == turn_id)
        .order_by(desc(RetrievalTrace.final_score))
    ).scalars().all()
    return {
        "turn": {
            "id": turn.id, "utterance": turn.utterance, "intent": turn.intent,
            "answer": turn.answer, "support_status": turn.support_status,
            "abstained": turn.abstained, "confidence": turn.confidence,
            "retrieval_ms": turn.retrieval_ms, "total_ms": turn.total_ms,
            "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens,
            "cost_usd": turn.cost_usd,
            "at": turn.created_at.isoformat(),
        },
        "candidates": [
            {"kind": t.kind, "ref_id": t.ref_id, "channel": t.channel,
             "rrf": t.rrf_score, "final": t.final_score,
             "breakdown": t.score_breakdown, "included": t.included,
             "exclusion_reason": t.exclusion_reason}
            for t in traces
        ],
    }


@router.get("/decisions")
def list_decisions(decision: str | None = None, rule_id: str | None = None,
                   limit: int = 100, db: Session = Depends(get_db)) -> dict:
    stmt = select(MemoryDecision).where(MemoryDecision.user_id == _user(db).id)
    if decision:
        stmt = stmt.where(MemoryDecision.decision == decision)
    if rule_id:
        stmt = stmt.where(MemoryDecision.rule_id == rule_id)
    rows = db.execute(
        stmt.order_by(desc(MemoryDecision.created_at)).limit(limit)
    ).scalars().all()
    return {
        "decisions": [
            {"id": d.id, "interaction_id": d.interaction_id, "decision": d.decision,
             "rule_id": d.rule_id, "rationale": d.rationale,
             "candidate": d.candidate, "resulting_memory_id": d.resulting_memory_id,
             "at": d.created_at.isoformat()}
            for d in rows
        ]
    }


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    user = _user(db)
    by_rule = db.execute(
        select(MemoryDecision.rule_id, MemoryDecision.decision, func.count())
        .where(MemoryDecision.user_id == user.id)
        .group_by(MemoryDecision.rule_id, MemoryDecision.decision)
    ).all()
    by_type = db.execute(
        select(Memory.type, Memory.status, func.count())
        .where(Memory.user_id == user.id).group_by(Memory.type, Memory.status)
    ).all()
    fenced = db.execute(
        select(IgnoredSpan.category, func.count())
        .where(IgnoredSpan.user_id == user.id).group_by(IgnoredSpan.category)
    ).all()
    return {
        "interactions": db.execute(
            select(func.count()).select_from(Interaction)
            .where(Interaction.user_id == user.id)
        ).scalar(),
        "memories_by_type": [
            {"type": t, "status": s, "count": c} for t, s, c in by_type
        ],
        "decisions_by_rule": [
            {"rule_id": r, "decision": d, "count": c} for r, d, c in by_rule
        ],
        "ignored_by_category": [{"category": c, "count": n} for c, n in fenced],
    }


class ImportRequest(BaseModel):
    file: str = Field(..., description="path inside the container, e.g. /corpus/x.jsonl")
    mapping: str | None = None
    learn: bool = True
    limit: int | None = None


@router.post("/admin/import")
def admin_import(req: ImportRequest, db: Session = Depends(get_db)) -> dict:
    return import_corpus(
        db, req.file, settings.kivi_user_handle,
        mapping_path=req.mapping, learn=req.learn, limit=req.limit,
    )
