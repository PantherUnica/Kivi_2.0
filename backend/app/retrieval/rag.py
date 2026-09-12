"""
Kivi's RAG pipeline (DECISIONS.md D-16, D-17, D-18).

Retrieval-Augmented Generation over the person's OWN history, in five stages:

    1. understand   intent, entities, app, and a resolved absolute time window
    2. retrieve     four channels over two corpora (memories + transcripts)
    3. fuse         RRF, then a deterministic rescore; exclusions recorded
    4. augment      build a context block of ONLY retrieved material, labelled
                    [M..] for memories and [E..] for episodes/transcripts
    5. verify       every citation must resolve; unsupported claims downgrade
                    the answer from `grounded` to `tentative`

Stage 4 is never reached if stage 3 produced nothing above threshold - that is
the abstention gate, and it is why "I don't have that" is a guarantee rather
than a hope.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import settings
from app.providers import get_embedder, get_llm
from app.retrieval import channels
from app.retrieval.fusion import Fused, apply_threshold, fuse

log = logging.getLogger(__name__)

UNDERSTAND_SYSTEM = """TASK: QUERY_UNDERSTANDING

Parse one spoken request to a voice assistant. Resolve any relative time
expression into an absolute UTC window using the supplied `now`.

Return ONLY JSON:
{"intent":"recall|search_history|restyle|correct_memory|forget_memory|what_do_you_know",
 "app": "Slack|Gmail|Notion|Linear|WhatsApp|null",
 "entities": ["proper nouns: projects, clients, people"],
 "time_window": {"start":"ISO8601","end":"ISO8601"} or null,
 "memory_types": ["style_rule","decision","commitment","project_context","routine"],
 "needs_memory": true|false}"""

ANSWER_SYSTEM = """TASK: ANSWER_FROM_CONTEXT

You are Kivi. Answer using ONLY the numbered context below. The context is the
person's own dictation history and the memories distilled from it.

Hard rules:
  - Cite the id of every piece of context you use, like [M3] or [E12].
  - If the context does not contain the answer, say so plainly. Never fill a
    gap with something plausible.
  - Do not add facts, names, numbers or dates that are not in the context.
  - Be brief. Two or three sentences is usually right.

Return ONLY JSON: {"answer": "...", "used_ids": ["M3","E12"]}"""

RESTYLE_SYSTEM = """TASK: RESTYLE

Rewrite the supplied text so it follows the person's own writing directives.
Change only form, never facts. Do not invent content.

Return ONLY JSON: {"text": "..."}"""


@dataclass
class RagResult:
    utterance: str
    intent: dict
    items: list[Fused]
    context_block: str
    id_map: dict[str, Fused]
    answer: str = ""
    support_status: str = "grounded"      # grounded | tentative | abstained
    abstained: bool = False
    confidence: float = 0.0
    used_ids: list[str] = field(default_factory=list)
    unresolved_ids: list[str] = field(default_factory=list)
    overridden: list[dict] = field(default_factory=list)
    retrieval_ms: int = 0
    total_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------- stage 1
def understand(utterance: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    llm = get_llm()
    res = llm.complete(
        UNDERSTAND_SYSTEM,
        json.dumps({"utterance": utterance, "now": now.isoformat()}),
        json_mode=True,
    )
    parsed = res.json() or {}
    return {
        "intent": parsed.get("intent") or "recall",
        "app": parsed.get("app") if parsed.get("app") not in ("null", "") else None,
        "entities": parsed.get("entities") or [],
        "time_window": parsed.get("time_window"),
        "memory_types": parsed.get("memory_types") or [],
        "needs_memory": parsed.get("needs_memory", True),
        "_tokens": res.prompt_tokens + res.completion_tokens,
        "_cost": res.cost_usd,
    }


# ------------------------------------------------------------- stage 2 + 3
def retrieve(db: Session, user_id: str, utterance: str, intent: dict,
             now: datetime | None = None) -> tuple[list[Fused], int]:
    started = time.perf_counter()
    qvec = get_embedder().embed([utterance])[0]
    tw = intent.get("time_window")

    cands: list[channels.Candidate] = []
    cands += channels.semantic_memories(db, user_id, qvec, settings.rag_top_k_memories)
    cands += channels.semantic_chunks(db, user_id, qvec, settings.rag_top_k_chunks, tw)
    cands += channels.lexical(db, user_id, utterance, settings.rag_top_k_chunks, tw)
    cands += channels.structured(db, user_id, intent, settings.rag_top_k_chunks)
    cands += channels.episodic(db, user_id, qvec, settings.rag_top_k_episodes, tw)

    items = apply_threshold(
        fuse(cands, intent, now, superseded_sources=_superseded_sources(db, user_id))
    )
    return items, int((time.perf_counter() - started) * 1000)


def _superseded_sources(db: Session, user_id: str) -> set[str]:
    """
    Interactions whose derived claim has since been reversed.

    Tracking a supersede in the memory layer is pointless if the raw transcript
    behind the old claim still wins retrieval and gets quoted as current.
    """
    rows = db.execute(
        sql_text(
            """
            SELECT DISTINCT e.interaction_id
            FROM memory_evidence e
            JOIN memories m ON m.id = e.memory_id
            WHERE m.user_id = :uid
              AND m.status IN ('superseded', 'contradicted')
            """
        ),
        {"uid": user_id},
    ).scalars().all()
    return set(rows)


# ------------------------------------------------------------- stage 4
MAX_MEMORIES_IN_CONTEXT = 7


def build_context(items: list[Fused], limit: int = 12) -> tuple[str, dict[str, Fused]]:
    """
    Assemble the augmented context. ONLY included items reach the model - an
    excluded memory is excluded from the prompt, not merely from the answer.

    Distilled memories almost always outscore raw transcripts, so an unbounded
    top-k fills entirely with memories and multi-hop questions - where the
    answer is spread across dictations that were never distilled - can never be
    answered. Memories are capped so transcripts always keep some of the window.
    """
    lines: list[str] = []
    id_map: dict[str, Fused] = {}
    m = e = 0

    eligible = [i for i in items if i.included]
    memories = [i for i in eligible if i.kind == "memory"][:MAX_MEMORIES_IN_CONTEXT]
    others = [i for i in eligible if i.kind != "memory"]
    chosen = sorted(memories + others, key=lambda f: -f.final)[:limit]

    for item in chosen:
        if item.kind == "memory":
            m += 1
            cid = f"M{m}"
            p = item.payload
            meta = f"type={p.get('type')} confidence={p.get('confidence')}"
            scope = p.get("scope") or {}
            if any(scope.values()):
                meta += f" scope={json.dumps({k: v for k, v in scope.items() if v})}"
            lines.append(f"[{cid}] MEMORY ({meta}): {item.text}")
        else:
            e += 1
            cid = f"E{e}"
            p = item.payload
            when = p.get("captured_at") or p.get("occurred_at")
            where = p.get("app") or ""
            dest = p.get("destination") or ""
            head = " ".join(str(x) for x in [when, where, dest] if x)
            lines.append(f"[{cid}] DICTATION ({head}): {item.text[:700]}")
        id_map[cid] = item

    return "\n".join(lines), id_map


# ------------------------------------------------------------- stage 5
CITATION = re.compile(r"\[([ME]\d+)\]")

CLAIMY = re.compile(r"[a-z]{3,}", re.I)

# Language that marks a statement as the speaker's guess rather than a fact.
HEDGED = re.compile(
    r"\bi think\b|\bprobably\b|\bi guess\b|\bmaybe\b|\bperhaps\b|"
    r"\bseems? like\b|\bi feel like\b|\bi suppose\b|\bnot sure\b|\bkind of\b",
    re.I,
)


def verify(answer: str, id_map: dict[str, Fused]) -> tuple[str, list[str], list[str]]:
    """
    Check the answer against what was actually retrieved.

    Returns (support_status, resolved_ids, unresolved_ids).

    A citation that does not resolve is the clearest possible hallucination
    signal, so it downgrades the answer rather than being quietly stripped.
    """
    cited = CITATION.findall(answer)
    resolved = [c for c in cited if c in id_map]
    unresolved = [c for c in cited if c not in id_map]

    if unresolved:
        return "tentative", resolved, unresolved

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
    substantive = [s for s in sentences if len(CLAIMY.findall(s)) >= 4]
    uncited = [s for s in substantive if not CITATION.search(s)]

    if not resolved:
        return "tentative", resolved, unresolved
    if uncited and len(uncited) >= max(1, len(substantive) // 2):
        return "tentative", resolved, unresolved
    return "grounded", resolved, unresolved


ABSTAIN_TEMPLATE = (
    "I don't have anything in your history about that. "
    "I'd rather say so than guess."
)

# Phrases in the live utterance that must beat a stored preference (D-18).
OVERRIDE_MARKERS = re.compile(
    r"\b(?:this time|just this once|ignore (?:my|the) (?:usual|preference|style)|"
    r"instead|for now|but use|actually use|override)\b",
    re.I,
)


def detect_overrides(utterance: str, items: list[Fused]) -> list[dict]:
    """
    Find stored style memories the current instruction overrules.

    They stay retrieved and visible, marked as overridden, because hiding them
    would make the behaviour look arbitrary.
    """
    if not OVERRIDE_MARKERS.search(utterance):
        return []
    overridden = []
    for item in items:
        if item.kind != "memory" or (item.payload.get("type") != "style_rule"):
            continue
        if item.included:
            item.included = False
            item.exclusion_reason = "overridden by the current instruction"
            overridden.append({"id": item.ref_id, "claim": item.text})
    return overridden


def answer(db: Session, user_id: str, utterance: str, *,
           now: datetime | None = None, intent: dict | None = None) -> RagResult:
    """The full RAG turn. This is what Hey Kivi calls."""
    t0 = time.perf_counter()
    now = now or datetime.now(timezone.utc)

    intent = intent or understand(utterance, now)
    items, retrieval_ms = retrieve(db, user_id, utterance, intent, now)

    overridden = detect_overrides(utterance, items)
    context_block, id_map = build_context(items)

    result = RagResult(
        utterance=utterance, intent=intent, items=items,
        context_block=context_block, id_map=id_map,
        retrieval_ms=retrieval_ms, overridden=overridden,
        prompt_tokens=int(intent.get("_tokens", 0)),
        cost_usd=float(intent.get("_cost", 0.0)),
    )

    # ---- the abstention gate (D-17) -------------------------------------
    # No context means the generator is never invoked. Not "asked to refuse" -
    # not called at all.
    if not id_map:
        result.answer = ABSTAIN_TEMPLATE
        result.support_status = "abstained"
        result.abstained = True
        result.confidence = 0.0
        result.total_ms = int((time.perf_counter() - t0) * 1000)
        result.notes.append(
            f"Abstained: no candidate cleared the support threshold "
            f"({settings.rag_support_threshold}). {len(items)} candidates were "
            f"retrieved and all were excluded."
        )
        return result

    llm = get_llm()
    res = llm.complete(
        ANSWER_SYSTEM,
        json.dumps(
            {
                "question": utterance,
                "wanted_types": intent.get("memory_types") or [],
                "context": [
                    {
                        "id": cid,
                        "text": f.text,
                        "kind": f.kind,
                        "type": (f.payload.get("type") if f.kind == "memory" else None),
                    }
                    for cid, f in id_map.items()
                ],
            }
        ),
        json_mode=True,
    )
    parsed = res.json() or {}
    text = (parsed.get("answer") or "").strip()

    result.prompt_tokens += res.prompt_tokens
    result.completion_tokens += res.completion_tokens
    result.cost_usd += res.cost_usd

    if not text:
        result.answer = ABSTAIN_TEMPLATE
        result.support_status = "abstained"
        result.abstained = True
        result.notes.append("Model produced no answer text; abstained rather than guessing.")
        result.total_ms = int((time.perf_counter() - t0) * 1000)
        return result

    status, resolved, unresolved = verify(text, id_map)

    # A hedge is not a fact (D-05). If every piece of supporting context was
    # itself hedged - "I think I'm probably more productive in the mornings" -
    # the answer may quote it, but it must never be presented as established.
    # Part One: offered back as a question, never a claim.
    supporting_text = " ".join(
        id_map[c].text for c in resolved if c in id_map
    ) or " ".join(f.text for f in result.items if f.included)
    if status == "grounded" and supporting_text and HEDGED.search(supporting_text):
        cited_texts = [id_map[c].text for c in resolved if c in id_map]
        if cited_texts and all(HEDGED.search(t) for t in cited_texts):
            status = "tentative"
            result.notes.append(
                "Everything supporting this was hedged when you said it, so Kivi "
                "is offering it back as a suggestion rather than a fact."
            )

    result.answer = text
    result.support_status = status
    result.used_ids = resolved
    result.unresolved_ids = unresolved

    supporting = [id_map[c] for c in resolved] or [i for i in items if i.included][:1]
    result.confidence = round(
        min(0.98, sum(s.final for s in supporting) / max(1, len(supporting))), 3
    )

    if unresolved:
        result.notes.append(
            f"Answer cited {unresolved} which were not retrieved. Marked tentative."
        )
    if overridden:
        result.notes.append(
            f"{len(overridden)} stored style memory(ies) overridden by the live instruction."
        )

    result.total_ms = int((time.perf_counter() - t0) * 1000)
    return result
