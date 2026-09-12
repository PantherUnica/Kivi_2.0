"""safe_text: keep the record verbatim, but never let the fence leak into retrieval

Revision ID: 0002
Revises: 0001

Found during evaluation: the sensitivity fence (D-06) stopped memory being
DERIVED from health / financial / relationship / emotional material, but the
raw transcript remained fully searchable - so Hey Kivi happily quoted a fenced
sentence straight back when asked "what do you remember about my salary?".

Stopping derivation is not enough. `interactions.safe_text` holds the text with
fenced sentences removed; it is the ONLY text the retrieval corpus is built
from and the only text an answer can quote. `raw_asr` and `formatted_text`
stay verbatim, because the record belongs to the user.
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE interactions ADD COLUMN safe_text TEXT")
    conn.exec_driver_sql("UPDATE interactions SET safe_text = formatted_text")
    conn.exec_driver_sql(
        "ALTER TABLE interaction_chunks ADD COLUMN fenced BOOLEAN NOT NULL DEFAULT FALSE"
    )
    conn.exec_driver_sql(
        "CREATE INDEX ix_chunks_fenced ON interaction_chunks (fenced) WHERE fenced = FALSE"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS ix_chunks_fenced")
    conn.exec_driver_sql("ALTER TABLE interaction_chunks DROP COLUMN fenced")
    conn.exec_driver_sql("ALTER TABLE interactions DROP COLUMN safe_text")
