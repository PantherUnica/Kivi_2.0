"""
Kivi database model.

Two layers, deliberately separate (DECISIONS.md D-02):

  CAPTURE  interactions / interaction_chunks / episodes
           Everything the user ever dictated. Stored verbatim, searchable
           forever, NEVER automatically a "memory".

  MEMORY   memories / memory_evidence
           A small set of distilled, typed, evidence-backed claims.

  AUDIT    memory_decisions / memory_events / ignored_spans
           Why anything was, or was not, remembered.

  RAG      hey_kivi_turns / retrieval_traces
           What was retrieved, what was excluded and why, at what cost.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings

EMBED_DIM = settings.embed_dim


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- enumerations
# Plain strings, not PG enums, so a new memory type never needs a migration;
# the policy engine is the authority on what is valid.

MEMORY_TYPES = ("style_rule", "decision", "commitment", "project_context", "routine")

EXPLICITNESS = ("explicit_statement", "observed_repetition", "inferred", "user_correction")

MEMORY_STATUS = (
    "provisional",    # known, but not yet trusted enough to use  (D-05)
    "active",
    "stale",
    "superseded",
    "contradicted",
    "forgotten",      # user-forgotten; excluded from retrieval, kept for audit (D-15)
    "expired",
)

# Categories that exist ONLY so the system can refuse them (D-06).
SENSITIVE_CATEGORIES = (
    "relationship_conflict",
    "emotional_state",
    "financial",
    "health",
)

DECISIONS = ("REMEMBER", "UPDATE", "IGNORE", "CONTRADICT", "ASK_CONFIRMATION", "PROVISIONAL")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    handle: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------ capture layer
class Interaction(Base):
    """One dictation (or Hey Kivi utterance). Stored verbatim, always."""

    __tablename__ = "interactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mode: Mapped[str] = mapped_column(String(24), default="dictation")
    app: Mapped[str | None] = mapped_column(String(64), index=True)
    destination: Mapped[str | None] = mapped_column(String(128), index=True)
    project_hint: Mapped[str | None] = mapped_column(String(128))

    raw_asr: Mapped[str] = mapped_column(Text)
    formatted_text: Mapped[str] = mapped_column(Text)
    # formatted_text minus any fenced sentence. The ONLY text retrieval reads
    # and the only text an answer may quote (migration 0002, D-06).
    safe_text: Mapped[str | None] = mapped_column(Text)
    style_used: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_corpus: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    chunks: Mapped[list["InteractionChunk"]] = relationship(
        back_populates="interaction", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="uq_interaction_external"),
        Index("ix_interaction_user_time", "user_id", "captured_at"),
    )


class InteractionChunk(Base):
    """Retrieval unit for the episodic half of the RAG corpus (D-16)."""

    __tablename__ = "interaction_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    interaction_id: Mapped[str] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    ord: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    fenced: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    interaction: Mapped[Interaction] = relationship(back_populates="chunks")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    client: Mapped[str | None] = mapped_column(String(128), index=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="active")

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_project_name"),)


class Episode(Base):
    """A cheap, one-per-interaction summary. An event, not a memory (D-02)."""

    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    interaction_id: Mapped[str] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    app: Mapped[str | None] = mapped_column(String(64), index=True)
    destination: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


# ------------------------------------------------------------- memory layer
class Memory(Base):
    """A typed, evidence-backed claim. NOT a chunk, NOT an embedding (D-03)."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    type: Mapped[str] = mapped_column(String(32), index=True)
    claim: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)

    explicitness: Mapped[str] = mapped_column(String(32), default="explicit_statement")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    corroboration_count: Mapped[int] = mapped_column(Integer, default=1)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    sensitivity: Mapped[str] = mapped_column(String(32), default="none")
    action_policy: Mapped[str] = mapped_column(String(32), default="answer_use")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forgotten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supersedes_id: Mapped[str | None] = mapped_column(String(36))
    superseded_by_id: Mapped[str | None] = mapped_column(String(36))

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    evidence: Mapped[list["MemoryEvidence"]] = relationship(
        back_populates="memory", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_memory_user_type_status", "user_id", "type", "status"),)


class MemoryEvidence(Base):
    """The exact quoted span that supports a claim. Provenance is not optional."""

    __tablename__ = "memory_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    interaction_id: Mapped[str] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    quote: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    memory: Mapped[Memory] = relationship(back_populates="evidence")


# -------------------------------------------------------------- audit layer
class MemoryDecision(Base):
    """One row per candidate considered, including every candidate REJECTED."""

    __tablename__ = "memory_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    interaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    candidate: Mapped[dict] = mapped_column(JSONB, default=dict)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    rule_id: Mapped[str] = mapped_column(String(48), index=True)
    rationale: Mapped[str] = mapped_column(Text)
    resulting_memory_id: Mapped[str | None] = mapped_column(String(36), index=True)

    model: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class MemoryEvent(Base):
    """Lifecycle log: created, corroborated, superseded, corrected, forgotten."""

    __tablename__ = "memory_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    memory_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(16), default="system")
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class IgnoredSpan(Base):
    """
    The deliberately-ignored log (D-06, D-22).

    Stores the CATEGORY and a hash, never the sensitive text. This table is a
    product surface ("Kivi chose not to remember"), not a debug log.
    """

    __tablename__ = "ignored_spans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    interaction_id: Mapped[str] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(48), index=True)
    reason: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(String(48))
    span_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ----------------------------------------------------------- hey kivi / RAG
class HeyKiviTurn(Base):
    __tablename__ = "hey_kivi_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    utterance: Mapped[str] = mapped_column(Text)
    intent: Mapped[dict] = mapped_column(JSONB, default=dict)
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    answer: Mapped[str] = mapped_column(Text, default="")
    support_status: Mapped[str] = mapped_column(String(24), default="grounded", index=True)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    retrieval_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class RetrievalTrace(Base):
    """
    Every candidate the RAG layer considered, with its score breakdown and,
    crucially, whether it was EXCLUDED and why (D-16).

    `included = false` is the row that answers "why did memory NOT affect this?"
    """

    __tablename__ = "retrieval_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("hey_kivi_turns.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    ref_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(24))
    rank_in_channel: Mapped[int] = mapped_column(Integer, default=0)
    raw_score: Mapped[float] = mapped_column(Float, default=0.0)
    rrf_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(128))


class DbGrowthSnapshot(Base):
    __tablename__ = "db_growth_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    table_name: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
