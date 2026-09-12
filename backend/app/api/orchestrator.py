"""
The Hey Kivi turn loop.

Understand once, route to one tool, persist the turn and its full retrieval
trace. Intent routing is deterministic given the parsed intent - the model
decides what was MEANT, not what the system is allowed to DO.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import HeyKiviTurn, RetrievalTrace
from app.retrieval import rag
from app.tools import registry

# "Actually I use bullets with Acme now" -> the new claim is the sentence itself.
CORRECTION_LEAD = re.compile(
    r"^\s*(?:actually|correction|scratch that|from now on)[,:\s]+", re.I
)
FORGET_LEAD = re.compile(
    r"^\s*(?:forget|stop using|drop|delete)\s+(?:that\s+)?", re.I
)


def persist_turn(db: Session, user_id: str, session_id: str, utterance: str,
                 intent: dict, tool_name: str, payload: dict,
                 result: rag.RagResult | None) -> str:
    turn = HeyKiviTurn(
        user_id=user_id,
        session_id=session_id,
        utterance=utterance,
        intent={k: v for k, v in intent.items() if not k.startswith("_")},
        tool_calls=[{"tool": tool_name}],
        answer=payload.get("answer") or payload.get("message") or payload.get("text") or "",
        support_status=payload.get("support_status", "grounded"),
        abstained=bool(payload.get("abstained")),
        confidence=float(payload.get("confidence") or 0.0),
        retrieval_ms=int((payload.get("metrics") or {}).get("retrieval_ms", 0)),
        total_ms=int((payload.get("metrics") or {}).get("total_ms",
                                                        payload.get("total_ms", 0))),
        prompt_tokens=int((payload.get("metrics") or {}).get("prompt_tokens", 0)),
        completion_tokens=int((payload.get("metrics") or {}).get("completion_tokens", 0)),
        cost_usd=float((payload.get("metrics") or {}).get("cost_usd", 0.0)),
    )
    db.add(turn)
    db.flush()

    # Persist EVERY candidate, included or not. The excluded rows are the ones
    # that answer "why did memory not affect this?" (D-16).
    if result is not None:
        for item in result.items:
            db.add(
                RetrievalTrace(
                    turn_id=turn.id,
                    kind=item.kind,
                    ref_id=item.ref_id,
                    channel="+".join(sorted(set(item.channels))),
                    raw_score=0.0,
                    rrf_score=item.rrf,
                    final_score=item.final,
                    score_breakdown=item.breakdown,
                    included=item.included,
                    exclusion_reason=item.exclusion_reason,
                )
            )
    db.commit()
    return turn.id


def handle(db: Session, user_id: str, utterance: str, *, session_id: str = "default",
           destination: str | None = None, app: str | None = None,
           text: str | None = None, now: datetime | None = None) -> dict:
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)

    intent = rag.understand(utterance, now)
    name = intent.get("intent", "recall")
    result: rag.RagResult | None = None

    if name == "forget_memory":
        target = FORGET_LEAD.sub("", utterance).strip(" .")
        payload = registry.forget(db, user_id, target)

    elif name == "correct_memory":
        new_claim = CORRECTION_LEAD.sub("", utterance).strip(" .")
        payload = registry.correct(db, user_id, new_claim, new_claim)

    elif name == "restyle":
        source = text
        if not source:
            # No text supplied: find what they are referring to first.
            found = registry.search_history(db, user_id, utterance, intent=intent, limit=1)
            source = found["results"][0]["text"] if found["results"] else ""
            payload = registry.restyle(
                db, user_id, source, destination=destination or _dest_from(intent),
                app=app or intent.get("app"), instruction=utterance,
            )
            payload["source"] = found["results"][0] if found["results"] else None
        else:
            payload = registry.restyle(
                db, user_id, source, destination=destination or _dest_from(intent),
                app=app or intent.get("app"), instruction=utterance,
            )

    elif name == "search_history":
        payload = registry.search_history(db, user_id, utterance, intent=intent)

    elif name == "what_do_you_know":
        payload = registry.what_do_you_know(db, user_id, " ".join(intent.get("entities") or []))

    else:
        result = rag.answer(db, user_id, utterance, now=now, intent=intent)
        payload = {"tool": "recall", **registry.result_dto(db, result)}

    turn_id = persist_turn(db, user_id, session_id, utterance, intent,
                           payload.get("tool", name), payload, result)

    payload["turn_id"] = turn_id
    payload.setdefault("intent", {k: v for k, v in intent.items() if not k.startswith("_")})
    payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return payload


def _dest_from(intent: dict) -> str | None:
    ents = intent.get("entities") or []
    return ents[0] if ents else None
