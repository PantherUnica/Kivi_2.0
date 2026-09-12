#!/usr/bin/env python3
"""
Kivi evaluation harness (DECISIONS.md D-23, D-24).

Two phases in one command:

    Phase A  ingestion  - did the system remember / ignore / corroborate /
                          supersede / fence the right things?
    Phase B  queries    - does Hey Kivi answer, cite, abstain and obey
                          corrections correctly?

Run inside the api container:
    python -m evaluation.run --corpus /corpus/kivi_corpus.jsonl

Outputs to evaluation/results/<timestamp>/
    report.md      human-readable, with every FAILURE printed in full
    results.jsonl  one line per case with the complete trace
    metrics.json   the headline numbers
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")

from sqlalchemy import func, select  # noqa: E402

from app.api import orchestrator  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.ingest.importer import get_or_create_user, import_corpus  # noqa: E402
from app.models import (  # noqa: E402
    IgnoredSpan,
    Interaction,
    Memory,
    MemoryDecision,
    MemoryEvidence,
)
from app.providers import get_embedder, get_llm  # noqa: E402
from app.tools import registry  # noqa: E402

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"


class Results:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, *, case_id: str, cls: str, phase: str, passed: bool,
            detail: str, evidence: dict | None = None) -> None:
        self.rows.append(
            {
                "case_id": case_id, "class": cls, "phase": phase,
                "passed": bool(passed), "detail": detail,
                "evidence": evidence or {},
            }
        )

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r["passed"])

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if not r["passed"]]


def _plant_to_interaction(db, user_id: str) -> dict[str, Interaction]:
    """Map planted corpus labels to the interactions they became."""
    rows = db.execute(
        select(Interaction).where(Interaction.user_id == user_id)
    ).scalars().all()
    out = {}
    for i in rows:
        plant = (i.meta or {}).get("plant")
        if plant:
            out[plant] = i
    return out


def phase_a(db, user_id: str, res: Results) -> dict:
    """Ingestion assertions."""
    spec = json.loads((CASES / "ingestion_assertions.json").read_text(encoding="utf-8"))
    plants = _plant_to_interaction(db, user_id)

    def decisions_for(interaction_id: str) -> list[MemoryDecision]:
        return db.execute(
            select(MemoryDecision).where(MemoryDecision.interaction_id == interaction_id)
        ).scalars().all()

    def memories_from(interaction_id: str) -> list[Memory]:
        return db.execute(
            select(Memory).join(MemoryEvidence, MemoryEvidence.memory_id == Memory.id)
            .where(MemoryEvidence.interaction_id == interaction_id).distinct()
        ).scalars().all()

    # ---- must_remember ---------------------------------------------------
    for a in spec["must_remember"]:
        inter = plants.get(a["plant"])
        if inter is None:
            res.add(case_id=f"A/remember/{a['plant']}", cls=a["class"], phase="A",
                    passed=False, detail="planted record not found in the database")
            continue
        mems = [m for m in memories_from(inter.id) if m.type == a["type"]]
        needles = [n.lower() for n in a.get("claim_contains", [])]
        hit = [m for m in mems if any(n in m.claim.lower() for n in needles)] if needles else mems
        res.add(
            case_id=f"A/remember/{a['plant']}", cls=a["class"], phase="A",
            passed=bool(hit),
            detail=(f"remembered as {hit[0].type} (conf {hit[0].confidence})"
                    if hit else
                    f"expected a {a['type']} memory containing {needles}; "
                    f"got {[(m.type, m.claim[:60]) for m in mems] or 'nothing'}"),
            evidence={"interaction": inter.external_id,
                      "decisions": [(d.decision, d.rule_id) for d in decisions_for(inter.id)],
                      "memory_id": hit[0].id if hit else None},
        )

    # ---- must_ignore -----------------------------------------------------
    for a in spec["must_ignore"]:
        inter = plants.get(a["plant"])
        if inter is None:
            res.add(case_id=f"A/ignore/{a['plant']}", cls=a["class"], phase="A",
                    passed=False, detail="planted record not found")
            continue
        mems = memories_from(inter.id)
        ds = decisions_for(inter.id)
        fired = [d.rule_id for d in ds if d.decision == "IGNORE"]
        passed = not mems
        res.add(
            case_id=f"A/ignore/{a['plant']}", cls=a["class"], phase="A", passed=passed,
            detail=("no memory derived" + (f"; rules {fired}" if fired else "; no candidate proposed")
                    if passed else
                    f"expected no memory, got {[m.claim[:60] for m in mems]}"),
            evidence={"interaction": inter.external_id,
                      "decisions": [(d.decision, d.rule_id, d.rationale[:90]) for d in ds]},
        )

    # ---- must_fence ------------------------------------------------------
    for a in spec["must_fence"]:
        inter = plants.get(a["plant"])
        if inter is None:
            res.add(case_id=f"A/fence/{a['plant']}", cls=a["class"], phase="A",
                    passed=False, detail="planted record not found")
            continue
        spans = db.execute(
            select(IgnoredSpan).where(IgnoredSpan.interaction_id == inter.id)
        ).scalars().all()
        logged = [s for s in spans if s.category == a["category"]]
        derived = memories_from(inter.id)
        leaked = [m for m in derived if m.sensitivity != "none"]
        passed = bool(logged) and not leaked
        res.add(
            case_id=f"A/fence/{a['plant']}", cls=a["class"], phase="A", passed=passed,
            detail=(f"fenced as '{a['category']}', {len(derived)} non-sensitive memory(ies) "
                    f"from the surviving text"
                    if passed else
                    f"expected an ignored_spans row for '{a['category']}'; "
                    f"found {[s.category for s in spans]}, leaked={[m.claim[:50] for m in leaked]}"),
            evidence={"interaction": inter.external_id,
                      "ignored": [s.category for s in spans]},
        )

    # ---- must_stay_provisional ------------------------------------------
    for a in spec["must_stay_provisional"]:
        inter = plants.get(a["plant"])
        if inter is None:
            res.add(case_id=f"A/provisional/{a['plant']}", cls=a["class"], phase="A",
                    passed=False, detail="planted record not found")
            continue
        mems = memories_from(inter.id)
        actives = [m for m in mems if m.status == "active"]
        passed = not actives
        res.add(
            case_id=f"A/provisional/{a['plant']}", cls=a["class"], phase="A", passed=passed,
            detail=("inference never reached active status" if passed else
                    f"an inference became active: {[m.claim[:70] for m in actives]}"),
            evidence={"interaction": inter.external_id,
                      "statuses": [(m.status, m.explicitness) for m in mems]},
        )

    # ---- must_corroborate ------------------------------------------------
    for a in spec["must_corroborate"]:
        prefix = a["plant_prefix"]
        inters = [i for p, i in plants.items() if p.startswith(prefix)]
        mem_ids: set[str] = set()
        for i in inters:
            for m in memories_from(i.id):
                if m.type == a["type"]:
                    mem_ids.add(m.id)
        mems = [db.get(Memory, mid) for mid in mem_ids]
        live = [m for m in mems if m and m.status in ("active", "superseded")]
        best = max((m.corroboration_count for m in live), default=0)
        passed = best >= a["min_corroborations"] and len(live) <= a["max_memories"]
        res.add(
            case_id=f"A/corroborate/{prefix}", cls=a["class"], phase="A", passed=passed,
            detail=(f"{len(inters)} statements converged on {len(live)} memory(ies), "
                    f"top corroboration {best}"),
            evidence={"claims": [(m.claim[:70], m.corroboration_count, m.confidence)
                                 for m in live if m]},
        )

    # ---- must_supersede --------------------------------------------------
    for a in spec["must_supersede"]:
        old_i, new_i = plants.get(a["old_plant"]), plants.get(a["new_plant"])
        if not old_i or not new_i:
            res.add(case_id=f"A/supersede/{a['old_plant']}", cls=a["class"], phase="A",
                    passed=False, detail="planted records not found")
            continue
        olds = memories_from(old_i.id)
        news = memories_from(new_i.id)
        superseded = [m for m in olds if m.status in ("superseded", "contradicted")]
        passed = bool(superseded) and bool([m for m in news if m.status == "active"])
        res.add(
            case_id=f"A/supersede/{a['old_plant']}", cls=a["class"], phase="A", passed=passed,
            detail=("old decision superseded by the newer one" if passed else
                    f"old statuses {[m.status for m in olds]}, "
                    f"new statuses {[m.status for m in news]}"),
            evidence={"old": [(m.claim[:70], m.status) for m in olds],
                      "new": [(m.claim[:70], m.status) for m in news]},
        )

    # ---- noise must distil to nothing ------------------------------------
    noise_spec = spec["must_produce_no_memory"]
    noise = db.execute(
        select(Interaction).where(Interaction.user_id == user_id)
        .order_by(Interaction.captured_at).limit(600)
    ).scalars().all()
    noise = [i for i in noise if not (i.meta or {}).get("plant")][: noise_spec["sample"]]
    with_mem = [i for i in noise if memories_from(i.id)]
    rate = len(with_mem) / max(1, len(noise))
    passed = rate <= noise_spec["max_memory_rate"]
    res.add(
        case_id="A/noise/no_memory", cls=noise_spec["class"], phase="A", passed=passed,
        detail=f"{len(with_mem)}/{len(noise)} noise records produced a memory "
               f"({rate:.0%}, ceiling {noise_spec['max_memory_rate']:.0%})",
        evidence={"examples": [i.formatted_text[:70] for i in with_mem[:5]]},
    )

    # ---- fence integrity, corpus-wide -------------------------------------
    fenced_ids = {
        s.interaction_id for s in db.execute(select(IgnoredSpan)).scalars().all()
    }
    leaked = []
    for iid in fenced_ids:
        for m in memories_from(iid):
            if m.sensitivity != "none":
                leaked.append((iid, m.claim[:60]))
    res.add(
        case_id="A/fence/integrity", cls="C", phase="A", passed=not leaked,
        detail=(f"{len(fenced_ids)} interactions carried fenced material; "
                f"{len(leaked)} sensitive memories derived (must be 0)"),
        evidence={"leaked": leaked[:5]},
    )

    return {
        "fenced_interactions": len(fenced_ids),
        "noise_memory_rate": round(rate, 4),
    }


def _text_of(payload: dict) -> str:
    return " ".join(
        str(payload.get(k) or "") for k in ("answer", "message", "text", "attribution")
    ).lower()


def phase_b(db, user_id: str, res: Results) -> dict:
    """Query and mutation behaviour."""
    spec = json.loads((CASES / "query_cases.json").read_text(encoding="utf-8"))
    latencies: list[int] = []
    retrieval_latencies: list[int] = []
    costs: list[float] = []
    supported = 0
    citable = 0
    abstain_correct = abstain_total = 0
    retrieved_memory_ids: set[str] = set()

    for case in spec["cases"]:
        exp = case["expect"]
        started = time.perf_counter()
        payload = orchestrator.handle(db, user_id, case["utterance"], session_id="eval")
        elapsed = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed)
        metrics = payload.get("metrics") or {}
        retrieval_latencies.append(int(metrics.get("retrieval_ms", 0)))
        costs.append(float(metrics.get("cost_usd", 0.0)))

        for c in payload.get("citations", []):
            if c.get("kind") == "memory":
                retrieved_memory_ids.add(c["ref_id"])

        answer = _text_of(payload)
        problems: list[str] = []

        if "tool" in exp and payload.get("tool") != exp["tool"]:
            problems.append(f"expected tool {exp['tool']}, got {payload.get('tool')}")

        if "support_status" in exp:
            got = payload.get("support_status")
            if got not in exp["support_status"]:
                problems.append(f"support_status {got} not in {exp['support_status']}")
            else:
                supported += 1

        if exp.get("answer_contains_any"):
            if not any(n.lower() in answer for n in exp["answer_contains_any"]):
                problems.append(f"answer missing any of {exp['answer_contains_any']}")

        for banned in exp.get("answer_not_contains", []):
            if banned.lower() in answer:
                problems.append(f"answer contained forbidden text {banned!r}")

        if exp.get("must_cite"):
            n = len(payload.get("used_ids") or [])
            if n < exp.get("min_citations", 1):
                problems.append(f"expected >= {exp.get('min_citations', 1)} citations, got {n}")
            else:
                citable += 1

        if "min_results" in exp and len(payload.get("results") or []) < exp["min_results"]:
            problems.append(f"expected >= {exp['min_results']} results")

        if "min_memories" in exp:
            total = payload.get("total", 0)
            if total < exp["min_memories"]:
                problems.append(f"expected >= {exp['min_memories']} memories, got {total}")

        if "abstained" in str(exp.get("support_status", "")):
            abstain_total += 1
            if payload.get("abstained"):
                abstain_correct += 1

        res.add(
            case_id=case["id"], cls=case["class"], phase="B", passed=not problems,
            detail="; ".join(problems) or "ok",
            evidence={
                "utterance": case["utterance"],
                "answer": (payload.get("answer") or payload.get("message") or "")[:400],
                "support_status": payload.get("support_status"),
                "used_ids": payload.get("used_ids"),
                "citations": [
                    {"id": c["citation_id"], "kind": c["kind"], "score": c["score"],
                     "text": c["text"][:160]}
                    for c in (payload.get("citations") or [])[:5]
                ],
                "excluded": (payload.get("excluded") or [])[:3],
                "turn_id": payload.get("turn_id"),
                "latency_ms": elapsed,
                "retrieval_ms": metrics.get("retrieval_ms"),
                "cost_usd": metrics.get("cost_usd"),
            },
        )

    mutation_stats = _mutations(db, user_id, res, spec)

    return {
        "latency_p50": int(statistics.median(latencies)) if latencies else 0,
        "latency_p95": int(sorted(latencies)[int(len(latencies) * 0.95) - 1]) if latencies else 0,
        "retrieval_p50": int(statistics.median(retrieval_latencies)) if retrieval_latencies else 0,
        "cost_total_usd": round(sum(costs), 6),
        "queries": len(spec["cases"]),
        "retrieved_memory_ids": retrieved_memory_ids,
        **mutation_stats,
    }


def _mutations(db, user_id: str, res: Results, spec: dict) -> dict:
    """Correction, forgetting, and live-instruction override (classes K, L, O)."""
    correction_latencies: list[int] = []

    for case in spec["mutations"]:
        exp = case["expect"]
        problems: list[str] = []
        evidence: dict = {"utterance": case["utterance"]}

        if case["kind"] == "correct":
            before = orchestrator.handle(db, user_id, case["setup_query"], session_id="eval")
            evidence["before"] = (before.get("answer") or "")[:200]

            started = time.perf_counter()
            out = registry.correct(db, user_id, case["utterance"], case["utterance"])
            latency = int((time.perf_counter() - started) * 1000)
            correction_latencies.append(latency)
            evidence["correction"] = {"ok": out.get("ok"), "message": out.get("message")}

            if not out.get("ok"):
                problems.append("correction did not resolve to any memory")
            else:
                old_id = (out.get("before") or {}).get("id")
                old = db.get(Memory, old_id) if old_id else None
                if old is not None and old.status != exp.get("old_claim_must_be", "superseded"):
                    problems.append(f"old memory status is {old.status}, expected superseded")

                after = orchestrator.handle(db, user_id, case["verify_query"], session_id="eval")
                evidence["after"] = (after.get("answer") or "")[:200]
                text = _text_of(after)
                if exp.get("verify_contains_any") and not any(
                    n.lower() in text for n in exp["verify_contains_any"]
                ):
                    problems.append(
                        f"after correcting, answer still missing {exp['verify_contains_any']}"
                    )

        elif case["kind"] == "forget":
            started = time.perf_counter()
            out = registry.forget(db, user_id, case["utterance"])
            latency = int((time.perf_counter() - started) * 1000)
            correction_latencies.append(latency)
            evidence["forget"] = {
                "ok": out.get("ok"),
                "verified_absent": out.get("verified_absent_from_retrieval"),
            }
            if not out.get("ok"):
                problems.append("forget did not resolve to any memory")
            elif exp.get("verified_absent_from_retrieval") and not out.get(
                "verified_absent_from_retrieval"
            ):
                problems.append("memory still appeared in retrieval after being forgotten")
            else:
                after = orchestrator.handle(db, user_id, case["verify_query"], session_id="eval")
                evidence["after"] = (after.get("answer") or "")[:200]
                text = _text_of(after)

                forgotten_id = (out.get("forgotten") or {}).get("id")
                mem_cites = [
                    c for c in (after.get("citations") or [])
                    if c.get("kind") == "memory" and c.get("ref_id") == forgotten_id
                ]
                evidence["memory_citations_after"] = len(mem_cites)
                if exp.get("no_memory_citation") and mem_cites:
                    problems.append(
                        "the forgotten memory was still cited after being forgotten"
                    )
                for banned in exp.get("verify_not_contains", []):
                    if banned.lower() in text:
                        problems.append(f"forgotten content still surfaced: {banned!r}")

        elif case["kind"] == "override":
            out = registry.restyle(
                db, user_id, case["text"], destination="Acme", app="Slack",
                instruction=case["utterance"],
            )
            evidence["restyle"] = {
                "attribution": out.get("attribution"),
                "overridden": out.get("overridden"),
                "applied": [a["claim"][:70] for a in (out.get("applied") or [])],
            }
            if exp.get("must_not_apply_stored_style") and out.get("applied"):
                problems.append(
                    "stored style was applied despite an explicit override in the instruction"
                )
            latency = 0

        res.add(case_id=case["id"], cls=case["class"], phase="B",
                passed=not problems, detail="; ".join(problems) or "ok", evidence=evidence)

    return {
        "correction_latency_p50_ms": int(statistics.median(correction_latencies))
        if correction_latencies else 0
    }


def compute_metrics(db, user_id: str, res: Results, phase_a_stats: dict,
                    phase_b_stats: dict, ingest_stats: dict) -> dict:
    total_memories = db.execute(
        select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
    ).scalar() or 0
    active = db.execute(
        select(func.count()).select_from(Memory)
        .where(Memory.user_id == user_id, Memory.status == "active")
    ).scalar() or 0
    interactions = db.execute(
        select(func.count()).select_from(Interaction).where(Interaction.user_id == user_id)
    ).scalar() or 0

    used = phase_b_stats.pop("retrieved_memory_ids", set())
    all_memory_ids = set(
        db.execute(select(Memory.id).where(Memory.user_id == user_id)).scalars().all()
    )
    by_phase = {}
    for row in res.rows:
        b = by_phase.setdefault(row["phase"], {"passed": 0, "total": 0})
        b["total"] += 1
        b["passed"] += int(row["passed"])

    by_class: dict[str, dict] = {}
    for row in res.rows:
        c = by_class.setdefault(row["class"], {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += int(row["passed"])

    grounded = [r for r in res.rows
                if r["phase"] == "B" and r["evidence"].get("support_status") == "grounded"]
    with_citations = [r for r in grounded if r["evidence"].get("used_ids")]

    abstain_rows = [r for r in res.rows if r["class"] in ("I", "J")]
    abstain_ok = sum(1 for r in abstain_rows if r["passed"])

    embedder = get_embedder()
    growth = ingest_stats.get("growth", [])
    total_bytes = sum(g["bytes"] for g in growth)

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "llm_provider": settings.llm_provider,
            "llm_model": getattr(get_llm(), "model", "mock-deterministic"),
            "embedding_provider": getattr(embedder, "name", "unknown"),
            "embedding_degraded": bool(getattr(embedder, "degraded", False)),
            "embed_dim": settings.embed_dim,
        },
        "totals": {
            "cases": len(res.rows),
            "passed": res.passed,
            "failed": len(res.failed),
            "pass_rate": round(res.passed / max(1, len(res.rows)), 4),
        },
        "by_phase": by_phase,
        "by_class": by_class,
        "memory": {
            "interactions_ingested": interactions,
            "memories_total": total_memories,
            "memories_active": active,
            "memories_per_100_interactions": round(total_memories / max(1, interactions) * 100, 2),
            # Denominator is every memory that has ever existed, and the
            # numerator is counted against the same set - otherwise memories
            # retrieved before a mutation forgot or superseded them divide by a
            # smaller "active" count and the ratio reads above 100%.
            "useful_memory_precision": round(
                len(used & all_memory_ids) / max(1, len(all_memory_ids)), 4
            ),
            "fenced_interactions": phase_a_stats.get("fenced_interactions", 0),
            "noise_memory_rate": phase_a_stats.get("noise_memory_rate", 0.0),
        },
        "quality": {
            "evidence_support_rate": round(
                len(with_citations) / max(1, len(grounded)), 4
            ) if grounded else 0.0,
            "abstention_accuracy": round(abstain_ok / max(1, len(abstain_rows)), 4),
            "grounded_answers": len(grounded),
        },
        "performance": {
            "query_latency_p50_ms": phase_b_stats.get("latency_p50", 0),
            "query_latency_p95_ms": phase_b_stats.get("latency_p95", 0),
            "retrieval_latency_p50_ms": phase_b_stats.get("retrieval_p50", 0),
            "correction_latency_p50_ms": phase_b_stats.get("correction_latency_p50_ms", 0),
            "ingest_elapsed_s": ingest_stats.get("elapsed_s", 0),
        },
        "cost": {
            "ingest_tokens": ingest_stats.get("tokens", 0),
            "ingest_cost_usd": round(ingest_stats.get("cost_usd", 0.0), 6),
            "query_cost_usd": phase_b_stats.get("cost_total_usd", 0.0),
            "cost_per_100_records_usd": round(
                ingest_stats.get("cost_usd", 0.0) / max(1, interactions) * 100, 6
            ),
            "cost_per_active_memory_usd": round(
                ingest_stats.get("cost_usd", 0.0) / max(1, active), 6
            ),
        },
        "database_growth": {
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1_048_576, 2),
            "bytes_per_interaction": int(total_bytes / max(1, interactions)),
            "tables": growth,
        },
    }


CLASS_NAMES = {
    "A": "Correct memory creation", "B": "Useful information remembered",
    "C": "Irrelevant / fenced information ignored", "D": "Duplicate memories",
    "E": "Contradictory memories", "F": "Outdated memories",
    "G": "Scope isolation", "H": "Multi-interaction retrieval",
    "I": "Unsupported questions", "J": "Hallucination resistance",
    "K": "Memory correction", "L": "Memory forgetting",
    "M": "Preference application", "N": "Evidence attribution",
    "O": "Current instruction overrides old memory",
}


def write_report(outdir: Path, res: Results, metrics: dict) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    with (outdir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for row in res.rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    (outdir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, default=str), encoding="utf-8"
    )

    t = metrics["totals"]
    p = metrics["providers"]
    lines = [
        "# Kivi evaluation report",
        "",
        f"Run at **{metrics['run_at']}**",
        "",
        f"- LLM provider: `{p['llm_provider']}` (`{p['llm_model']}`)",
        f"- Embeddings: `{p['embedding_provider']}` at {p['embed_dim']} dims"
        + ("  **DEGRADED: hashing fallback, retrieval is lexical only**"
           if p["embedding_degraded"] else ""),
        "",
        f"## Result: {t['passed']}/{t['cases']} passed ({t['pass_rate']:.0%})",
        "",
        "| Phase | Passed | Total |",
        "|---|---:|---:|",
    ]
    for phase, v in sorted(metrics["by_phase"].items()):
        name = "A - ingestion" if phase == "A" else "B - queries"
        lines.append(f"| {name} | {v['passed']} | {v['total']} |")

    lines += ["", "## By evaluation class", "",
              "| Class | What it tests | Passed | Total |", "|---|---|---:|---:|"]
    for cls in sorted(metrics["by_class"]):
        v = metrics["by_class"][cls]
        flag = "" if v["passed"] == v["total"] else "  **FAIL**"
        lines.append(
            f"| {cls} | {CLASS_NAMES.get(cls, '-')} | {v['passed']}{flag} | {v['total']} |"
        )

    m, q, perf, cost, growth = (metrics["memory"], metrics["quality"],
                                metrics["performance"], metrics["cost"],
                                metrics["database_growth"])
    lines += [
        "", "## Metrics", "",
        "| Metric | Value |", "|---|---:|",
        f"| Interactions ingested | {m['interactions_ingested']} |",
        f"| Memories total / active | {m['memories_total']} / {m['memories_active']} |",
        f"| Memories per 100 interactions | {m['memories_per_100_interactions']} |",
        f"| Useful memory precision | {q and m['useful_memory_precision']:.2%} |",
        f"| Noise -> memory rate (must stay low) | {m['noise_memory_rate']:.2%} |",
        f"| Fenced interactions | {m['fenced_interactions']} |",
        f"| Evidence support rate | {q['evidence_support_rate']:.2%} |",
        f"| Abstention accuracy | {q['abstention_accuracy']:.2%} |",
        f"| Query latency p50 / p95 | {perf['query_latency_p50_ms']} ms / "
        f"{perf['query_latency_p95_ms']} ms |",
        f"| Retrieval latency p50 | {perf['retrieval_latency_p50_ms']} ms |",
        f"| Memory correction latency p50 | {perf['correction_latency_p50_ms']} ms |",
        f"| Ingest wall time | {perf['ingest_elapsed_s']} s |",
        f"| Ingest tokens | {cost['ingest_tokens']:,} |",
        f"| Ingest cost | ${cost['ingest_cost_usd']:.4f} |",
        f"| Cost per 100 records | ${cost['cost_per_100_records_usd']:.4f} |",
        f"| Cost per active memory | ${cost['cost_per_active_memory_usd']:.5f} |",
        f"| Database size | {growth['total_mb']} MB "
        f"({growth['bytes_per_interaction']:,} bytes/interaction) |",
    ]

    failures = res.failed
    lines += ["", f"## Failures ({len(failures)})", ""]
    if not failures:
        lines.append("None.")
    else:
        lines.append(
            "Printed in full. A failing case is more informative than an aggregate."
        )
        for row in failures:
            lines += [
                "", f"### {row['case_id']}  (class {row['class']} - "
                    f"{CLASS_NAMES.get(row['class'], '')})",
                "", f"**Why it failed:** {row['detail']}", "",
                "```json",
                json.dumps(row["evidence"], indent=2, default=str)[:2500],
                "```",
            ]

    lines += [
        "", "---", "",
        "Full per-case traces, including retrieved candidates and their score",
        "breakdowns, are in `results.jsonl`. Headline numbers are in `metrics.json`.",
        "Any answer's live trace is at `GET /api/turns/<turn_id>/trace`.",
    ]

    (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="/corpus/kivi_corpus.jsonl")
    ap.add_argument("--mapping", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-ingest", action="store_true",
                    help="evaluate against the database as it already stands")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out or (ROOT / "results" / stamp))

    res = Results()
    with session_scope() as db:
        user = get_or_create_user(db, settings.kivi_user_handle)

        if args.skip_ingest:
            ingest_stats = {"elapsed_s": 0, "tokens": 0, "cost_usd": 0.0, "growth": []}
            print("skipping ingest; evaluating the existing database")
        else:
            print(f"ingesting {args.corpus} ...")
            ingest_stats = import_corpus(
                db, args.corpus, settings.kivi_user_handle,
                mapping_path=args.mapping, learn=True, limit=args.limit,
                source_label="eval",
            )
            print(f"  {ingest_stats['interactions_created']} interactions, "
                  f"{ingest_stats['memories_created']} memories, "
                  f"{ingest_stats['candidates_ignored']} ignored, "
                  f"{ingest_stats['fenced_spans']} fenced "
                  f"({ingest_stats['elapsed_s']}s)")

        print("phase A: ingestion assertions ...")
        a_stats = phase_a(db, user.id, res)

        print("phase B: query + mutation cases ...")
        b_stats = phase_b(db, user.id, res)

        metrics = compute_metrics(db, user.id, res, a_stats, b_stats, ingest_stats)

    write_report(outdir, res, metrics)

    t = metrics["totals"]
    print(f"\n{t['passed']}/{t['cases']} passed ({t['pass_rate']:.0%})")
    for row in res.failed:
        print(f"  FAIL {row['case_id']:<34} {row['detail'][:90]}")
    print(f"\nreport  : {outdir / 'report.md'}")
    print(f"traces  : {outdir / 'results.jsonl'}")
    print(f"metrics : {outdir / 'metrics.json'}")

    return 0 if not res.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
