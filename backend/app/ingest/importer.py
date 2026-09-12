"""
Corpus import (JSONL or CSV) and the ingest run.

The reviewer's path:
    python -m app.cli import --file /corpus/theirs.jsonl --mapping their_map.json

Idempotent on external_id: re-importing the same corpus updates rather than
duplicates, so a reviewer who runs it twice does not get a doubled database.
"""
from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.chunker import chunk
from app.ingest.mapping import Mapping
from app.memory import sensitive
from app.memory.store import process_interaction, snapshot_growth
from app.models import Episode, Interaction, Project, User
from app.providers import get_embedder

log = logging.getLogger(__name__)


def resolve_corpus_path(path: str) -> Path:
    """
    Locate a corpus file robustly.

    Git Bash on Windows rewrites a leading "/corpus/..." into a Windows path
    before Docker ever sees it, so an absolute path typed by a reviewer can
    arrive mangled. Rather than make them debug that, try the obvious
    candidates and fail with a message that lists what was tried.
    """
    p = Path(path)
    if p.exists():
        return p

    name = p.name
    candidates = [
        Path("/app") / path.lstrip("/"),
        Path("/app/corpus") / name,
        Path("/app/evaluation") / name,
        Path("/corpus") / name,
        Path.cwd() / path.lstrip("/"),
    ]
    for cand in candidates:
        if cand.exists():
            log.warning("corpus path %s not found; using %s", path, cand)
            return cand

    raise FileNotFoundError(
        f"corpus not found: {path}. Tried: "
        + ", ".join(str(c) for c in [p, *candidates])
    )


def read_records(path: str) -> Iterator[dict]:
    p = resolve_corpus_path(path)

    if p.suffix.lower() in (".jsonl", ".ndjson"):
        with p.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    log.error("skipping malformed JSONL line %d: %s", line_no, exc)
    elif p.suffix.lower() == ".csv":
        with p.open(encoding="utf-8", newline="") as fh:
            yield from csv.DictReader(fh)
    elif p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        yield from (data if isinstance(data, list) else data.get("records", []))
    else:
        raise ValueError(f"unsupported corpus format: {p.suffix} (use .jsonl, .json or .csv)")


def get_or_create_user(db: Session, handle: str) -> User:
    user = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
    if user is None:
        user = User(handle=handle, display_name=handle.title())
        db.add(user)
        db.flush()
    return user


def _episode_title(text: str) -> str:
    first = (text or "").strip().split(".")[0]
    return (first[:120] or "Dictation").strip()


def import_corpus(db: Session, path: str, user_handle: str, *,
                  mapping_path: str | None = None, source_label: str = "corpus",
                  learn: bool = True, limit: int | None = None,
                  progress_every: int = 50) -> dict:
    """Import, chunk, embed, and (optionally) run the memory pipeline."""
    mapping = Mapping.load(mapping_path)
    embedder = get_embedder()
    user = get_or_create_user(db, user_handle)

    started = time.perf_counter()
    stats = {
        "records_read": 0, "interactions_created": 0, "interactions_updated": 0,
        "chunks": 0, "memories_created": 0, "memories_updated": 0,
        "candidates_ignored": 0, "confirmations_pending": 0, "fenced_spans": 0,
        "tokens": 0, "cost_usd": 0.0, "skipped_empty": 0,
        "embedding_provider": getattr(embedder, "name", "unknown"),
        "embedding_degraded": bool(getattr(embedder, "degraded", False)),
    }

    for raw in read_records(path):
        if limit and stats["records_read"] >= limit:
            break
        stats["records_read"] += 1
        rec = mapping.normalise(raw)
        if rec.get("client"):
            rec["meta"] = {**rec["meta"], "client": rec["client"]}

        if not rec["formatted_text"].strip() and not rec["raw_asr"].strip():
            stats["skipped_empty"] += 1
            continue

        existing = None
        if rec["external_id"]:
            existing = db.execute(
                select(Interaction).where(
                    Interaction.user_id == user.id,
                    Interaction.external_id == rec["external_id"],
                )
            ).scalar_one_or_none()

        if existing is not None:
            interaction = existing
            for key in ("captured_at", "raw_asr", "formatted_text", "app",
                        "destination", "project_hint", "style_used", "duration_ms", "mode"):
                setattr(interaction, key, rec[key])
            interaction.meta = rec["meta"]
            interaction.chunks.clear()
            db.flush()
            stats["interactions_updated"] += 1
        else:
            interaction = Interaction(
                user_id=user.id, source_corpus=source_label, meta=rec["meta"],
                **{k: rec[k] for k in (
                    "external_id", "captured_at", "raw_asr", "formatted_text", "app",
                    "destination", "project_hint", "style_used", "duration_ms", "mode")},
            )
            db.add(interaction)
            db.flush()
            stats["interactions_created"] += 1

        # ---- capture layer: chunks + one episode ------------------------
        # The record keeps the verbatim text; the RETRIEVAL corpus is built
        # from safe_text, so a fenced sentence can never be quoted back (D-06).
        verbatim = interaction.formatted_text or interaction.raw_asr
        safe, fenced_spans = sensitive.redact(verbatim)
        interaction.safe_text = safe
        body = safe
        pieces = chunk(body)
        if pieces:
            vecs = embedder.embed([p["text"] for p in pieces])
            from app.models import InteractionChunk

            for piece, vec in zip(pieces, vecs, strict=False):
                db.add(InteractionChunk(interaction_id=interaction.id, embedding=vec, **piece))
            stats["chunks"] += len(pieces)

        project = None
        if interaction.project_hint:
            project = db.execute(
                select(Project).where(
                    Project.user_id == user.id, Project.name == interaction.project_hint
                )
            ).scalar_one_or_none()
            if project is None:
                project = Project(
                    user_id=user.id, name=interaction.project_hint,
                    client=(interaction.meta or {}).get("client"),
                    first_seen_at=interaction.captured_at,
                    last_seen_at=interaction.captured_at,
                )
                db.add(project)
                db.flush()
            else:
                project.last_seen_at = max(
                    project.last_seen_at or interaction.captured_at, interaction.captured_at
                )

        title = _episode_title(body)
        summary = body[:600]
        db.add(
            Episode(
                user_id=user.id, interaction_id=interaction.id, title=title,
                summary=summary, occurred_at=interaction.captured_at,
                app=interaction.app, destination=interaction.destination,
                project_id=project.id if project else None,
                embedding=embedder.embed([f"{title}. {summary}"])[0],
            )
        )

        # ---- memory layer ----------------------------------------------
        if learn:
            result = process_interaction(db, interaction)
            stats["memories_created"] += len(result["created"])
            stats["memories_updated"] += len(result["updated"])
            stats["candidates_ignored"] += len(result["ignored"])
            stats["confirmations_pending"] += len(result["asked"])
            stats["fenced_spans"] += len(result["fenced"])
            stats["tokens"] += result["tokens"]
            stats["cost_usd"] += result["cost_usd"]

        if stats["records_read"] % progress_every == 0:
            db.commit()
            log.info(
                "ingested %d records | %d memories | %d ignored | %d fenced",
                stats["records_read"], stats["memories_created"],
                stats["candidates_ignored"], stats["fenced_spans"],
            )

    db.commit()
    stats["elapsed_s"] = round(time.perf_counter() - started, 2)
    stats["growth"] = snapshot_growth(db, f"after-{source_label}")
    db.commit()
    return stats
