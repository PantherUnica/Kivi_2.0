"""
Operator CLI. Everything the reviewer needs, with no dashboard work.

    python -m app.cli import   --file /corpus/kivi_corpus.jsonl
    python -m app.cli ask      "what do you know about how I write for Acme?"
    python -m app.cli stats
    python -m app.cli reset    --yes
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import text as sql_text

from app.config import settings
from app.db import engine, session_scope
from app.ingest.importer import get_or_create_user, import_corpus

logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
log = logging.getLogger("kivi")


def cmd_import(args: argparse.Namespace) -> int:
    with session_scope() as db:
        stats = import_corpus(
            db, args.file, settings.kivi_user_handle,
            mapping_path=args.mapping, learn=not args.no_learn,
            limit=args.limit, source_label=args.label,
        )
    print(json.dumps(stats, indent=2, default=str))
    if stats.get("embedding_degraded"):
        print(
            "\nWARNING: embeddings fell back to hashing (no model could be loaded). "
            "Retrieval is lexical-only. See DECISIONS.md D-09.",
            file=sys.stderr,
        )
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from app.api import orchestrator

    with session_scope() as db:
        user = get_or_create_user(db, settings.kivi_user_handle)
        result = orchestrator.handle(db, user.id, args.utterance, session_id="cli")
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\n  {result.get('answer') or result.get('message') or result.get('text')}\n")
        print(f"  support : {result.get('support_status', '-')}")
        print(f"  cited   : {', '.join(result.get('used_ids', [])) or '-'}")
        m = result.get("metrics", {})
        print(f"  timing  : retrieval {m.get('retrieval_ms', 0)}ms "
              f"/ total {result.get('elapsed_ms', 0)}ms")
        print(f"  trace   : GET /api/turns/{result.get('turn_id')}/trace\n")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from app.api.routes import stats as stats_route

    with session_scope() as db:
        print(json.dumps(stats_route(db), indent=2, default=str))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print("refusing to wipe without --yes", file=sys.stderr)
        return 1
    tables = [
        "retrieval_traces", "hey_kivi_turns", "memory_events", "memory_decisions",
        "ignored_spans", "memory_evidence", "memories", "episodes",
        "interaction_chunks", "interactions", "projects", "db_growth_snapshots",
    ]
    with engine.begin() as conn:
        conn.execute(sql_text("TRUNCATE " + ", ".join(tables) + " CASCADE"))
    print(f"cleared {len(tables)} tables; schema and users left intact")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="kivi")
    sub = p.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="import a transcript corpus")
    imp.add_argument("--file", required=True)
    imp.add_argument("--mapping", default=None, help="field-mapping JSON")
    imp.add_argument("--label", default="corpus")
    imp.add_argument("--limit", type=int, default=None)
    imp.add_argument("--no-learn", action="store_true",
                     help="capture only; run no memory extraction")
    imp.set_defaults(func=cmd_import)

    ask = sub.add_parser("ask", help="one Hey Kivi turn")
    ask.add_argument("utterance")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=cmd_ask)

    st = sub.add_parser("stats", help="memory / decision counts")
    st.set_defaults(func=cmd_stats)

    rs = sub.add_parser("reset", help="truncate all data, keep the schema")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(func=cmd_reset)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
