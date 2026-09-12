#!/usr/bin/env python3
"""
Reproducible seed. Equivalent to the import command in RUN.md section 7, kept
as a script so `make seed` and the compose one-liner have a single entry point.

    docker compose exec api python seed/seed.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app.config import settings          # noqa: E402
from app.db import session_scope         # noqa: E402
from app.ingest.importer import import_corpus  # noqa: E402

CORPUS = "corpus/kivi_corpus.jsonl"


def main() -> int:
    with session_scope() as db:
        stats = import_corpus(db, CORPUS, settings.kivi_user_handle,
                              learn=True, source_label="seed")
    # Import is idempotent on the record id, so a second run updates rather
    # than duplicating - report both counts or a re-run looks like it failed.
    print(
        f"seeded {stats['interactions_created']} new + "
        f"{stats['interactions_updated']} updated interactions -> "
        f"{stats['memories_created']} memories created, "
        f"{stats['memories_updated']} corroborated, "
        f"{stats['candidates_ignored']} ignored, "
        f"{stats['fenced_spans']} fenced  ({stats['elapsed_s']}s)"
    )
    if stats.get("embedding_degraded"):
        print("WARNING: hashing embeddings in use; retrieval is lexical only.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
