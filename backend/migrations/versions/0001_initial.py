"""initial kivi schema: capture / memory / audit / rag layers

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

from app.config import settings
from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DIM = settings.embed_dim


def upgrade() -> None:
    conn = op.get_bind()

    # pgvector must exist before any Vector column is created.
    conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    Base.metadata.create_all(bind=conn)

    # ---- full-text columns (the lexical RAG channel, D-16) -----------------
    # Generated columns keep the index in lockstep with the source text with
    # no trigger to maintain and no way for them to drift.
    fts = [
        ("interactions", "formatted_text"),
        ("interaction_chunks", "text"),
        ("memories", "claim"),
    ]
    for table, col in fts:
        conn.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN tsv tsvector "
            f"GENERATED ALWAYS AS (to_tsvector('english', coalesce({col}, ''))) STORED"
        )
        conn.exec_driver_sql(f"CREATE INDEX ix_{table}_tsv ON {table} USING GIN (tsv)")

    conn.exec_driver_sql(
        "ALTER TABLE episodes ADD COLUMN tsv tsvector GENERATED ALWAYS AS "
        "(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, ''))) STORED"
    )
    conn.exec_driver_sql("CREATE INDEX ix_episodes_tsv ON episodes USING GIN (tsv)")

    # ---- vector indexes (the semantic RAG channel) ------------------------
    # HNSW with cosine distance. Built after create_all so the columns exist.
    for table in ("interaction_chunks", "episodes", "memories"):
        conn.exec_driver_sql(
            f"CREATE INDEX ix_{table}_embedding ON {table} "
            f"USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )

    # ---- trigram index for fuzzy proper-noun matching ---------------------
    conn.exec_driver_sql(
        "CREATE INDEX ix_projects_name_trgm ON projects USING GIN (name gin_trgm_ops)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    Base.metadata.drop_all(bind=conn)
