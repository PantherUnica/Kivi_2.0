"""
The four retrieval channels of Kivi's RAG layer (DECISIONS.md D-16).

    semantic    pgvector cosine over memory claims AND interaction chunks
    lexical     Postgres full-text, which catches proper nouns that dense
                vectors blur ("Acme", "Halo", "Priya")
    structured  hard SQL filters from query understanding (time / app / project)
    episodic    time-anchored episodes, for "the dictation I did on Thursday"

Each returns a ranked list of Candidate rows. Nothing is fused here - fusion
and reranking happen in fusion.py so the channel scores stay inspectable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Episode, Interaction, Memory


@dataclass
class Candidate:
    kind: str                  # memory | chunk | episode
    ref_id: str
    channel: str
    rank: int
    raw_score: float
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


def _window(sql: str, time_window: dict | None, column: str) -> tuple[str, dict]:
    params: dict = {}
    if time_window and time_window.get("start"):
        sql += f" AND {column} >= :tw_start AND {column} <= :tw_end"
        params["tw_start"] = time_window["start"]
        params["tw_end"] = time_window["end"]
    return sql, params


# --------------------------------------------------------------- semantic
def semantic_memories(db: Session, user_id: str, qvec: list[float],
                      limit: int) -> list[Candidate]:
    rows = db.execute(
        sql_text(
            """
            SELECT id, claim, type, scope, confidence, status,
                   corroboration_count, last_confirmed_at,
                   1 - (embedding <=> CAST(:qv AS vector)) AS score
            FROM memories
            WHERE user_id = :uid
              AND embedding IS NOT NULL
              AND status IN ('active', 'provisional')
            ORDER BY embedding <=> CAST(:qv AS vector)
            LIMIT :lim
            """
        ),
        {"qv": str(qvec), "uid": user_id, "lim": limit},
    ).mappings().all()
    return [
        Candidate("memory", r["id"], "semantic", i, float(r["score"]), r["claim"], dict(r))
        for i, r in enumerate(rows)
    ]


def semantic_chunks(db: Session, user_id: str, qvec: list[float], limit: int,
                    time_window: dict | None = None) -> list[Candidate]:
    sql = """
        SELECT c.id, c.text, c.interaction_id, i.captured_at, i.app, i.destination,
               1 - (c.embedding <=> CAST(:qv AS vector)) AS score
        FROM interaction_chunks c
        JOIN interactions i ON i.id = c.interaction_id
        WHERE i.user_id = :uid AND c.embedding IS NOT NULL
    """
    sql, params = _window(sql, time_window, "i.captured_at")
    sql += " ORDER BY c.embedding <=> CAST(:qv AS vector) LIMIT :lim"
    rows = db.execute(
        sql_text(sql), {"qv": str(qvec), "uid": user_id, "lim": limit, **params}
    ).mappings().all()
    return [
        Candidate("chunk", r["id"], "semantic", i, float(r["score"]), r["text"], dict(r))
        for i, r in enumerate(rows)
    ]


# ---------------------------------------------------------------- lexical
def lexical(db: Session, user_id: str, query: str, limit: int,
            time_window: dict | None = None) -> list[Candidate]:
    """
    Full-text over both corpora. This is the channel that saves the system on
    proper nouns, where a 384-dim embedding of "Acme" and "Northwind" sit far
    closer together than they should.
    """
    out: list[Candidate] = []
    if not query.strip():
        return out

    mem_rows = db.execute(
        sql_text(
            """
            SELECT id, claim, type, scope, confidence, status, corroboration_count,
                   last_confirmed_at,
                   ts_rank(tsv, plainto_tsquery('english', :q)) AS score
            FROM memories
            WHERE user_id = :uid
              AND status IN ('active','provisional')
              AND tsv @@ plainto_tsquery('english', :q)
            ORDER BY score DESC LIMIT :lim
            """
        ),
        {"q": query, "uid": user_id, "lim": limit},
    ).mappings().all()
    out += [
        Candidate("memory", r["id"], "lexical", i, float(r["score"]), r["claim"], dict(r))
        for i, r in enumerate(mem_rows)
    ]

    sql = """
        SELECT c.id, c.text, c.interaction_id, i.captured_at, i.app, i.destination,
               ts_rank(c.tsv, plainto_tsquery('english', :q)) AS score
        FROM interaction_chunks c
        JOIN interactions i ON i.id = c.interaction_id
        WHERE i.user_id = :uid AND c.tsv @@ plainto_tsquery('english', :q)
    """
    sql, params = _window(sql, time_window, "i.captured_at")
    sql += " ORDER BY score DESC LIMIT :lim"
    chunk_rows = db.execute(
        sql_text(sql), {"q": query, "uid": user_id, "lim": limit, **params}
    ).mappings().all()
    out += [
        Candidate("chunk", r["id"], "lexical", i, float(r["score"]), r["text"], dict(r))
        for i, r in enumerate(chunk_rows)
    ]
    return out


# ------------------------------------------------------------- structured
def structured(db: Session, user_id: str, intent: dict, limit: int) -> list[Candidate]:
    """
    Hard filters, no similarity at all.

    "the Slack dictation last Thursday afternoon" is a SQL query, not a vector
    search, and pretending otherwise is how RAG systems lose precise questions.
    """
    tw = intent.get("time_window")
    app = intent.get("app")
    entities = intent.get("entities") or []
    if not (tw or app or entities):
        return []

    sql = """
        SELECT i.id, COALESCE(i.safe_text, i.formatted_text) AS formatted_text,
               i.captured_at, i.app, i.destination, i.project_hint
        FROM interactions i
        WHERE i.user_id = :uid
    """
    params: dict = {"uid": user_id, "lim": limit}

    if app:
        sql += " AND lower(i.app) = lower(:app)"
        params["app"] = app
    sql, twp = _window(sql, tw, "i.captured_at")
    params.update(twp)

    if entities:
        clauses = []
        for n, ent in enumerate(entities[:4]):
            key = f"ent{n}"
            clauses.append(
                f"(COALESCE(i.safe_text, i.formatted_text) ILIKE :{key} "
                f"OR i.project_hint ILIKE :{key} OR i.destination ILIKE :{key})"
            )
            params[key] = f"%{ent}%"
        sql += " AND (" + " OR ".join(clauses) + ")"

    sql += " ORDER BY i.captured_at DESC LIMIT :lim"
    rows = db.execute(sql_text(sql), params).mappings().all()

    return [
        Candidate(
            "chunk", r["id"], "structured", i,
            1.0 - (i * 0.05),                      # rank-ordered, recency first
            r["formatted_text"], dict(r) | {"interaction_id": r["id"]},
        )
        for i, r in enumerate(rows)
    ]


# --------------------------------------------------------------- episodic
def episodic(db: Session, user_id: str, qvec: list[float], limit: int,
             time_window: dict | None = None) -> list[Candidate]:
    sql = """
        SELECT e.id, e.title, e.summary, e.occurred_at, e.app, e.destination,
               e.interaction_id,
               1 - (e.embedding <=> CAST(:qv AS vector)) AS score
        FROM episodes e
        WHERE e.user_id = :uid AND e.embedding IS NOT NULL
    """
    sql, params = _window(sql, time_window, "e.occurred_at")
    sql += " ORDER BY e.embedding <=> CAST(:qv AS vector) LIMIT :lim"
    rows = db.execute(
        sql_text(sql), {"qv": str(qvec), "uid": user_id, "lim": limit, **params}
    ).mappings().all()
    return [
        Candidate(
            "episode", r["id"], "episodic", i, float(r["score"]),
            f"{r['title']}. {r['summary']}", dict(r),
        )
        for i, r in enumerate(rows)
    ]


def style_rules_for(db: Session, user_id: str, destination: str | None,
                    app: str | None, project: str | None = None) -> list[Memory]:
    """
    Fetch the style memories that apply to a destination (D-20).

    Scoped first, general second. A rule learned for Acme is never applied to
    Northwind just because it is the only rule we have (D-13 / R6).
    """
    rows = db.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.type == "style_rule",
            Memory.status == "active",
        ).order_by(Memory.confidence.desc())
    ).scalars().all()

    def matches(m: Memory) -> int:
        """
        2 = this rule is FOR this audience; 1 = merely the same app; 0 = no.

        A person says "write it for Acme", not "write it for #halo-build", so
        the client and project are matched as well as the literal destination.
        A rule learned in one context is still never widened to all (R6) - this
        only recognises the several names the same context goes by.
        """
        scope = m.scope or {}
        wanted = {w.lower() for w in (destination, project) if w}
        have = {
            str(scope.get(k)).lower()
            for k in ("destination", "client", "project")
            if scope.get(k)
        }
        if wanted and have and any(
            w in h or h in w for w in wanted for h in have
        ):
            return 2
        if app and (scope.get("app") or "").lower() == app.lower():
            return 1
        return 0

    scored = [(matches(m), m) for m in rows]
    exact = [m for rank, m in scored if rank == 2]
    if not exact:
        return [m for rank, m in scored if rank == 1]

    # Several rules can share a client - "no bullets for Acme" was learned in
    # Slack, "acceptance criteria first" in Linear. Both are Acme rules, but
    # they are not the same voice. Narrow to one surface rather than merging
    # them, or a preference silently widens past the context it was learned in
    # (R6 keeps them separate rows; this keeps them separate in USE).
    if app:
        same_app = [m for m in exact if (m.scope or {}).get("app", "").lower() == app.lower()]
        if same_app:
            return same_app

    by_app: dict[str, list[Memory]] = {}
    for m in exact:
        by_app.setdefault((m.scope or {}).get("app") or "", []).append(m)
    if len(by_app) > 1:
        best = max(by_app.values(), key=lambda g: max(x.confidence for x in g))
        return best
    return exact
