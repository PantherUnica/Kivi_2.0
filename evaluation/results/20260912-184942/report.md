# Kivi evaluation report

Run at **2026-09-12T18:50:03.115198+00:00**

- LLM provider: `mock` (`mock-deterministic`)
- Embeddings: `fastembed` at 384 dims

## Result: 48/48 passed (100%)

| Phase | Passed | Total |
|---|---:|---:|
| A - ingestion | 27 | 27 |
| B - queries | 21 | 21 |

## By evaluation class

| Class | What it tests | Passed | Total |
|---|---|---:|---:|
| A | Correct memory creation | 3 | 3 |
| B | Useful information remembered | 6 | 6 |
| C | Irrelevant / fenced information ignored | 21 | 21 |
| D | Duplicate memories | 2 | 2 |
| E | Contradictory memories | 1 | 1 |
| F | Outdated memories | 1 | 1 |
| G | Scope isolation | 2 | 2 |
| H | Multi-interaction retrieval | 3 | 3 |
| I | Unsupported questions | 2 | 2 |
| J | Hallucination resistance | 2 | 2 |
| K | Memory correction | 1 | 1 |
| L | Memory forgetting | 1 | 1 |
| M | Preference application | 1 | 1 |
| N | Evidence attribution | 1 | 1 |
| O | Current instruction overrides old memory | 1 | 1 |

## Metrics

| Metric | Value |
|---|---:|
| Interactions ingested | 520 |
| Memories total / active | 15 / 12 |
| Memories per 100 interactions | 2.88 |
| Useful memory precision | 86.67% |
| Noise -> memory rate (must stay low) | 0.00% |
| Fenced interactions | 14 |
| Evidence support rate | 100.00% |
| Abstention accuracy | 100.00% |
| Query latency p50 / p95 | 55 ms / 64 ms |
| Retrieval latency p50 | 19 ms |
| Memory correction latency p50 | 46 ms |
| Ingest wall time | 17.18 s |
| Ingest tokens | 228,273 |
| Ingest cost | $0.0000 |
| Cost per 100 records | $0.0000 |
| Cost per active memory | $0.00000 |
| Database size | 5.55 MB (11,185 bytes/interaction) |

## Failures (0)

None.

---

Full per-case traces, including retrieved candidates and their score
breakdowns, are in `results.jsonl`. Headline numbers are in `metrics.json`.
Any answer's live trace is at `GET /api/turns/<turn_id>/trace`.