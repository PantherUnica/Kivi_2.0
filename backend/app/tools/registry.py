"""
The six Hey Kivi tools (DECISIONS.md D-16, D-20, D-21).

Deliberately narrow. `retrieve_context` is NOT a tool - it is an orchestrator
stage, because the model should not be able to choose not to ground itself.

    search_history      find interactions by concept / time / app / project
    recall              answer a question with citations, or abstain
    restyle             rewrite text using the destination-scoped style memory
    what_do_you_know    conversational read of what has been learned
    correct_memory      supersede a claim and propagate
    forget_memory       deactivate, verifiably
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.memory.store import correct_memory, forget_memory
from app.models import Interaction, Memory, MemoryEvidence
from app.providers import get_llm
from app.retrieval import channels, rag

log = logging.getLogger(__name__)


def _evidence_for(db: Session, memory_id: str) -> list[dict]:
    rows = db.execute(
        select(MemoryEvidence, Interaction)
        .join(Interaction, Interaction.id == MemoryEvidence.interaction_id)
        .where(MemoryEvidence.memory_id == memory_id)
        .order_by(MemoryEvidence.captured_at)
    ).all()
    return [
        {
            "quote": ev.quote,
            "captured_at": inter.captured_at.isoformat(),
            "app": inter.app,
            "destination": inter.destination,
            "interaction_id": inter.id,
            "char_start": ev.char_start,
            "char_end": ev.char_end,
        }
        for ev, inter in rows
    ]


def memory_dto(db: Session, m: Memory, *, with_evidence: bool = True) -> dict:
    return {
        "id": m.id,
        "type": m.type,
        "claim": m.claim,
        "scope": m.scope or {},
        "explicitness": m.explicitness,
        "confidence": round(m.confidence, 3),
        "corroboration_count": m.corroboration_count,
        "status": m.status,
        "sensitivity": m.sensitivity,
        "action_policy": m.action_policy,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "last_confirmed_at": m.last_confirmed_at.isoformat() if m.last_confirmed_at else None,
        "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        "supersedes_id": m.supersedes_id,
        "superseded_by_id": m.superseded_by_id,
        "evidence": _evidence_for(db, m.id) if with_evidence else [],
    }


# ------------------------------------------------------------------- tools
def search_history(db: Session, user_id: str, query: str, *, intent: dict | None = None,
                   limit: int = 10) -> dict:
    """Find source interactions. Episodic, not distilled."""
    started = time.perf_counter()
    intent = intent or rag.understand(query)
    items, retrieval_ms = rag.retrieve(db, user_id, query, intent)

    hits, seen = [], set()
    for item in items:
        if not item.included or item.kind == "memory":
            continue
        iid = item.payload.get("interaction_id") or item.ref_id
        if iid in seen:
            continue
        seen.add(iid)
        inter = db.get(Interaction, iid)
        if inter is None:
            continue
        hits.append(
            {
                "interaction_id": inter.id,
                "captured_at": inter.captured_at.isoformat(),
                "app": inter.app,
                "destination": inter.destination,
                "project": inter.project_hint,
                "text": inter.safe_text or inter.formatted_text,
                "raw_asr": inter.raw_asr,
                "score": item.final,
                "channels": item.breakdown.get("channels", []),
            }
        )
        if len(hits) >= limit:
            break

    return {
        "tool": "search_history",
        "query": query,
        "intent": intent,
        "results": hits,
        "retrieval_ms": retrieval_ms,
        "total_ms": int((time.perf_counter() - started) * 1000),
    }


def recall(db: Session, user_id: str, question: str, *, intent: dict | None = None) -> dict:
    """Answer from memory with citations, or abstain (D-17)."""
    result = rag.answer(db, user_id, question, intent=intent)
    return {"tool": "recall", **result_dto(db, result)}


def result_dto(db: Session, result: rag.RagResult) -> dict:
    """Shape a RAG result for the API, carrying its full provenance."""
    citations = []
    for cid, item in result.id_map.items():
        entry = {
            "citation_id": cid,
            "kind": item.kind,
            "ref_id": item.ref_id,
            "text": item.text[:600],
            "score": item.final,
            "breakdown": item.breakdown,
            "used": cid in result.used_ids,
        }
        if item.kind == "memory":
            mem = db.get(Memory, item.ref_id)
            if mem is not None:
                entry["memory"] = memory_dto(db, mem)
        else:
            iid = item.payload.get("interaction_id") or item.ref_id
            inter = db.get(Interaction, iid)
            if inter is not None:
                entry["interaction"] = {
                    "id": inter.id,
                    "captured_at": inter.captured_at.isoformat(),
                    "app": inter.app,
                    "destination": inter.destination,
                    "text": inter.safe_text or inter.formatted_text,
                }
        citations.append(entry)

    excluded = [
        {
            "kind": i.kind, "ref_id": i.ref_id, "text": i.text[:200],
            "score": i.final, "reason": i.exclusion_reason,
        }
        for i in result.items if not i.included
    ][:20]

    return {
        "utterance": result.utterance,
        "intent": {k: v for k, v in result.intent.items() if not k.startswith("_")},
        "answer": result.answer,
        "support_status": result.support_status,
        "abstained": result.abstained,
        "confidence": result.confidence,
        "citations": citations,
        "used_ids": result.used_ids,
        "unresolved_ids": result.unresolved_ids,
        "excluded": excluded,
        "overridden": result.overridden,
        "notes": result.notes,
        "metrics": {
            "retrieval_ms": result.retrieval_ms,
            "total_ms": result.total_ms,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cost_usd": round(result.cost_usd, 6),
            "candidates_considered": len(result.items),
            "candidates_used": len(result.id_map),
        },
    }


def restyle(db: Session, user_id: str, text: str, *, destination: str | None = None,
            app: str | None = None, project: str | None = None,
            instruction: str = "") -> dict:
    """
    Rewrite text in the person's own voice for a destination (D-20).

    Style is applied automatically and ALWAYS attributed. A live instruction
    that conflicts with the stored rule wins, and says so.
    """
    started = time.perf_counter()
    rules = channels.style_rules_for(db, user_id, destination, app, project)

    overridden = []
    if instruction and rag.OVERRIDE_MARKERS.search(instruction):
        overridden = [{"id": m.id, "claim": m.claim} for m in rules]
        rules = []

    if not rules:
        return {
            "tool": "restyle",
            "text": text,
            "applied": [],
            "overridden": overridden,
            "attribution": (
                "Used your live instruction; the stored style memory was overridden."
                if overridden else
                f"I don't have a style memory for {destination or app or 'this destination'} "
                f"yet, so this is unchanged."
            ),
            "total_ms": int((time.perf_counter() - started) * 1000),
        }

    directives: list[str] = []
    for rule in rules:
        claim = rule.claim.split(":", 1)[-1]
        directives += [d.strip() for d in claim.split(",") if d.strip()]

    llm = get_llm()
    res = llm.complete(
        rag.RESTYLE_SYSTEM,
        json.dumps({"text": text, "directives": directives,
                    "destination": destination, "instruction": instruction}),
        json_mode=True,
    )
    parsed = res.json() or {}
    out = (parsed.get("text") or "").strip() or text

    applied = [memory_dto(db, m) for m in rules]
    sources = sum(len(a["evidence"]) for a in applied)
    dates = sorted(
        e["captured_at"][:10] for a in applied for e in a["evidence"]
    )
    span = f" between {dates[0]} and {dates[-1]}" if len(dates) > 1 else ""

    return {
        "tool": "restyle",
        "text": out,
        "original": text,
        "applied": applied,
        "overridden": overridden,
        "attribution": (
            f"Rewritten in your {destination or app or 'usual'} voice: "
            f"{', '.join(sorted(set(directives)))}. "
            f"Learned from {sources} dictation{'s' if sources != 1 else ''}{span}."
        ),
        "metrics": {
            "total_ms": int((time.perf_counter() - started) * 1000),
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "cost_usd": round(res.cost_usd, 6),
        },
    }


def what_do_you_know(db: Session, user_id: str, topic: str = "", *,
                     include_ignored: bool = True) -> dict:
    """
    Conversational read of what has been learned (D-21).

    The survey's #1 professional trust ask was "clearly show me what's stored"
    (87%). This is that, as a sentence rather than a settings page.
    """
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.status.in_(("active", "provisional")),
    ).order_by(Memory.type, Memory.confidence.desc())
    memories = db.execute(stmt).scalars().all()

    if topic.strip():
        terms = {t for t in topic.lower().split() if len(t) > 2}
        memories = [
            m for m in memories
            if terms & set(m.claim.lower().split())
            or terms & {str(v).lower() for v in (m.scope or {}).values() if v}
        ] or memories

    grouped: dict[str, list[dict]] = {}
    for m in memories:
        grouped.setdefault(m.type, []).append(memory_dto(db, m))

    out = {
        "tool": "what_do_you_know",
        "topic": topic,
        "groups": grouped,
        "total": len(memories),
    }

    if include_ignored:
        from app.models import IgnoredSpan

        rows = db.execute(
            select(IgnoredSpan).where(IgnoredSpan.user_id == user_id)
            .order_by(IgnoredSpan.occurred_at.desc()).limit(50)
        ).scalars().all()
        by_cat: dict[str, list[str]] = {}
        for r in rows:
            by_cat.setdefault(r.category, []).append(r.occurred_at.date().isoformat())
        # Category and date only. The content is never surfaced (D-22).
        out["chose_not_to_remember"] = [
            {"category": cat, "count": len(dates), "dates": sorted(set(dates))[-5:]}
            for cat, dates in sorted(by_cat.items(), key=lambda kv: -len(kv[1]))
        ]
    return out


def _resolve_target(db: Session, user_id: str, description: str) -> Memory | None:
    """Find the memory a sentence like 'forget that I prefer bullets' refers to."""
    items, _ = rag.retrieve(db, user_id, description, rag.understand(description))
    for item in items:
        if item.kind == "memory":
            mem = db.get(Memory, item.ref_id)
            if mem is not None and mem.status in ("active", "provisional"):
                return mem
    return None


def correct(db: Session, user_id: str, description: str, new_claim: str) -> dict:
    """Supersede a memory with a user correction, and propagate (D-15)."""
    started = time.perf_counter()
    target = _resolve_target(db, user_id, description)
    if target is None:
        return {
            "tool": "correct_memory",
            "ok": False,
            "message": "I couldn't find a memory matching that. Nothing was changed.",
            "total_ms": int((time.perf_counter() - started) * 1000),
        }

    before = memory_dto(db, target)
    replacement = correct_memory(db, target, new_claim,
                                 note=f"user said: {description}")
    db.commit()
    return {
        "tool": "correct_memory",
        "ok": True,
        "message": "Got it. I've updated that.",
        "before": before,
        "after": memory_dto(db, replacement),
        "total_ms": int((time.perf_counter() - started) * 1000),
    }


def forget(db: Session, user_id: str, description: str) -> dict:
    """
    Deactivate a memory and prove retrieval no longer returns it (D-15).

    The verification round-trip is the point: a delete you cannot check is not
    a delete, and 78% of the survey named deletion their top trust requirement.
    """
    started = time.perf_counter()
    target = _resolve_target(db, user_id, description)
    if target is None:
        return {
            "tool": "forget_memory",
            "ok": False,
            "message": "I couldn't find a memory matching that. Nothing was changed.",
            "total_ms": int((time.perf_counter() - started) * 1000),
        }

    snapshot = memory_dto(db, target)
    forget_memory(db, target, note=f"user said: {description}")
    db.commit()

    # Verify, don't assert.
    items, _ = rag.retrieve(db, user_id, snapshot["claim"], rag.understand(snapshot["claim"]))
    still_visible = any(
        i.kind == "memory" and i.ref_id == snapshot["id"] and i.included for i in items
    )

    return {
        "tool": "forget_memory",
        "ok": True,
        "message": "Done. I'll no longer use that.",
        "forgotten": snapshot,
        "verified_absent_from_retrieval": not still_visible,
        "correction_latency_ms": int((time.perf_counter() - started) * 1000),
        "total_ms": int((time.perf_counter() - started) * 1000),
    }


TOOLS = {
    "search_history": search_history,
    "recall": recall,
    "restyle": restyle,
    "what_do_you_know": what_do_you_know,
    "correct_memory": correct,
    "forget_memory": forget,
}
