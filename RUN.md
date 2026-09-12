# RUN.md

## Primary review method: **containerised application and database (Docker Compose)**

Everything — Postgres 16 + pgvector, the FastAPI backend, and the web client — starts with one command. No local Python, Node, or database is required, and **no API key is required** for the documented path.

---

## 1. Required runtimes and versions

| Requirement | Version | Notes |
|---|---|---|
| Docker Engine | 24+ | Docker Desktop 4.30+ on macOS/Windows |
| Docker Compose | v2 (`docker compose`, not `docker-compose`) | ships with Docker Desktop |
| Disk | ~2.5 GB | images plus the embedding-model cache |
| RAM | 4 GB free | |
| Internet | first run only | to pull images and the 130 MB embedding model |

Nothing else. Python 3.12 and Node 22 live inside the images.

---

## 2. Every required environment variable

Copy the template. It is complete, and already set for a zero-credential run:

```bash
cp .env.example .env
```

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` (no key) / `sarvam` / `openai_compatible` |
| `SARVAM_API_KEY` | empty | **only** if `LLM_PROVIDER=sarvam` |
| `SARVAM_BASE_URL` | `https://api.sarvam.ai/v1` | |
| `SARVAM_MODEL` | `sarvam-m` | open-weight, Qwen3-derived |
| `LLM_BASE_URL` | empty | **only** if `LLM_PROVIDER=openai_compatible`, e.g. `http://host.docker.internal:11434/v1` |
| `LLM_MODEL` | `qwen3:8b` | the local open-weight target |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` / `qwen3` / `hashing` |
| `EMBED_DIM` | `384` | **must** match the model; changing it requires a reset |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | |
| `ASR_PROVIDER` | `replay` | no speech recognition is built — see README |
| `DATABASE_URL` | set by compose | |
| `API_PORT` / `WEB_PORT` | `8000` / `5173` | |
| `KIVI_USER_HANDLE` | `maya` | the single demo user |

Policy and RAG thresholds (`POLICY_*`, `RAG_*`) are documented inline in `.env.example`. The defaults are the ones every committed result was produced with.

> **To run with a real model instead of `mock`:** set `LLM_PROVIDER=sarvam` and `SARVAM_API_KEY=...` in `.env`, then `docker compose up -d --force-recreate api`. A plain `restart` does **not** reload `.env`.

---

## 3. Install dependencies

Nothing to install by hand. This builds both images:

```bash
docker compose build
```

---

## 4. Create, migrate and seed the database

Migrations run automatically when the API starts. To do it explicitly:

```bash
docker compose up -d db
docker compose run --rm api alembic upgrade head
```

Seed data — the 520-record corpus — is committed at `corpus/kivi_corpus.jsonl`. To regenerate it deterministically:

```bash
docker compose run --rm api python corpus/generate_corpus.py
```

---

## 5. Start every required process

```bash
docker compose up -d
```

This starts three containers: `kivi-db`, `kivi-api`, `kivi-web`. Confirm health:

```bash
curl http://localhost:8000/api/health
```

---

## 6. The interface to open

**http://localhost:5173**

OpenAPI docs are at **http://localhost:8000/docs**.

---

## 7. Load the demo corpus, then the interactions to try

Load it first — the app is deliberately empty until something is ingested:

```bash
docker compose exec api python -m app.cli import --file corpus/kivi_corpus.jsonl
```

About 25 seconds with `mock`. Then, in the UI:

**Desk** — dictate something and watch the quiet "Kivi learned…" note. Try:

> `For Acme I always keep updates to three short paragraphs, no bullet points.`
>
> `Sorry, running late, just left a doctor's appointment. The Halo spec is ready.`

The second is captured verbatim, but the health clause is refused and never becomes memory.

**Hey Kivi** — every preset button is one of the evaluation cases:

| Ask | What it demonstrates |
|---|---|
| *What do you know about how I write for Acme?* | style memory with its evidence |
| *How do I write emails to Northwind?* | scope isolation — the Acme rule must not leak |
| *What did we decide about the Halo data layer?* | the week-6 reversal supersedes week 2 |
| *What is the total Halo budget?* | multi-hop across three separate dictations |
| *What do you know about my health?* | the fence, enforced at query time |
| *What is Priya's manager called?* | abstention — never stated in the corpus |
| *Actually I use bullet points with Acme now.* | correction; re-ask to see behaviour change |
| *Forget that I write Linear tickets with a one-line summary.* | verified forgetting |

Open **"why did Kivi say this?"** under any answer for the retrieval trace: every candidate, its score breakdown, and the reason excluded ones were excluded.

**What Kivi knows** — memories with sources, inline forget, and the "Kivi chose not to remember" panel.

---

## 8. Run the candidate evaluation

```bash
docker compose exec api python -m evaluation.run --corpus corpus/kivi_corpus.jsonl
```

It ingests, then asserts. To evaluate the database as it already stands, add `--skip-ingest`.

The exit code is `0` only when every case passes.

> **The evaluation deliberately changes memory.** Classes K and L test correction and forgetting, so after a run the Acme style rule reads *"Actually I use bullet points with Acme now"* and the Linear rule is forgotten — that is the test passing, not a fault. For a clean demo state afterwards:
>
> ```bash
> docker compose exec api python -m app.cli reset --yes
> docker compose exec api python -m app.cli import --file corpus/kivi_corpus.jsonl
> ```

---

## 9. Importing another corpus

JSONL, JSON or CSV. Put the file in `corpus/` (bind-mounted to `/app/corpus`) and run:

```bash
docker compose exec api python -m app.cli import --file corpus/YOUR_FILE.jsonl
```

Field names are matched automatically against common aliases (`transcript`, `asr_text`, `llm_output`, `timestamp`, `channel`, and so on). If yours differ, write a mapping file — no code changes needed:

```bash
cp corpus/mapping.example.json corpus/my_mapping.json   # then edit it
docker compose exec api python -m app.cli import \
  --file corpus/YOUR_FILE.jsonl --mapping corpus/my_mapping.json
```

Any field that is not mapped is preserved verbatim in `interactions.meta`. Import is idempotent on the record id: re-running updates rather than duplicates.

To evaluate against an imported corpus:

```bash
docker compose exec api python -m evaluation.run \
  --corpus corpus/YOUR_FILE.jsonl --mapping corpus/my_mapping.json
```

---

## 10. Where results and memory state can be inspected

**Files** — `evaluation/results/<timestamp>/`

- `report.md` — pass/fail by class, all metrics, and **every failure printed in full**
- `results.jsonl` — one line per case: query, retrieved set, evidence, decision, answer, latency, cost
- `metrics.json` — the headline numbers

**In the product** — *What Kivi knows*, and *"why did Kivi say this?"* beneath any answer.

**Over HTTP**

| Endpoint | Shows |
|---|---|
| `GET /api/health` | providers in use, row counts |
| `GET /api/stats` | memories by type and status, decisions by rule, refusals by category |
| `GET /api/memories` | every memory with its evidence |
| `GET /api/memories/{id}` | full lifecycle: events plus the decisions that produced it |
| `GET /api/decisions?decision=IGNORE` | everything deliberately not remembered, with the rule that fired |
| `GET /api/interactions/{id}` | one dictation, its candidates, and its fenced spans |
| `GET /api/turns/{id}/trace` | the full RAG trace including excluded candidates and reasons |

**In SQL**

```bash
docker compose exec db psql -U kivi -d kivi
```

```sql
SELECT type, status, count(*) FROM memories GROUP BY 1,2 ORDER BY 1;
SELECT rule_id, decision, count(*) FROM memory_decisions GROUP BY 1,2 ORDER BY 3 DESC;
SELECT category, count(*) FROM ignored_spans GROUP BY 1;          -- what it refused
SELECT claim, confidence, corroboration_count FROM memories WHERE status='active';
```

---

## 11. Resetting

Clear all data, keep the schema:

```bash
docker compose exec api python -m app.cli reset --yes
```

Full teardown including the database volume and the model cache:

```bash
docker compose down -v
```

Then start again from step 5.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `corpus not found` naming a `C:/Program Files/Git/...` path | Git Bash rewrites leading `/paths` | use the relative form shown above (`corpus/…`); the importer also resolves this automatically |
| Health shows `"embedding_degraded": true` | the embedding model could not be fetched | retrieval falls back to lexical-only hashing and says so loudly; re-run with network access, or set `EMBEDDING_PROVIDER=hashing` deliberately |
| A `.env` change had no effect | `restart` does not reload `env_file` | `docker compose up -d --force-recreate api` |
| Port already in use | | change `API_PORT` / `WEB_PORT` in `.env` |
