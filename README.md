# Kivi — semantic memory

> **Kivi remembers what you said and how you say it — not who you are.**

Golden Goose, Part Two. A working, end-to-end demonstration of semantic memory for a voice-first interface, built on the position set out in [`part-one/positioning-statement.md`](part-one/positioning-statement.md) and [`part-one/vision-document.md`](part-one/vision-document.md).

**Start here:** [`RUN.md`](RUN.md) — one command, no API key.
**Why anything is the way it is:** [`DECISIONS.md`](DECISIONS.md) — 29 logged decisions, including the four the evaluation forced.

---

## The product in one paragraph

You dictate. Kivi writes, and never rewrites your words. In the background it distils a *small* number of durable claims — how you write for each client, what you decided, what you promised — each one carrying the exact sentences it came from. Later you ask Hey Kivi something, and it answers from that record with citations you can open, or tells you plainly that your history does not contain the answer. Health, money, relationships and how you were feeling are never turned into memory at all, and Kivi shows you that it declined.

Over the 520-record demo corpus that comes to **14 memories from 520 dictations** — under three per hundred. The restraint is the product.

---

## What it does, concretely

| | |
|---|---|
| **Learns** | writing style scoped per client, decisions, commitments with due dates, project context, recurring routines |
| **Refuses** | health, financial, relationship-conflict and emotional-state material — before extraction, and again at retrieval |
| **Ignores** | one-off statements, hedged guesses, and 456 records of ordinary work chatter that produced nothing |
| **Answers** | with citations resolving to exact quoted spans, or abstains |
| **Changes** | applies your destination-scoped voice on rewrite, always attributed, always beaten by a live instruction |
| **Corrects** | "Actually I use bullets with Acme now" supersedes in one sentence; "forget that" is verified, not asserted |

---

## Use cases it was built around

Everything below is a real path through the system, exercised by the evaluation.

1. **"Rewrite this for the Acme check-in."** Kivi applies the Acme voice and says so: *"avoids bullet points, keeps it to three short paragraphs. Learned from 5 dictations between 2026-07-06 and 2026-08-17."* Ask for Northwind instead and you get a different voice — the Acme rule never leaks.
2. **"What did we decide about the Halo data layer?"** Week 2 chose Postgres; week 6 reversed to DynamoDB. Kivi answers with the reversal, and the superseded claim — plus the transcript behind it — is visibly demoted rather than deleted.
3. **"What is the total Halo budget?"** Split across three dictations that were deliberately never distilled into memory. Answered by retrieving the transcripts.
4. **"What do you know about my health?"** *"I don't have anything in your history about that."* Fourteen fenced mentions exist in the corpus; none became memory, and none can be quoted back.
5. **"What is Priya's manager called?"** Never stated. Kivi abstains rather than reaching for the nearest plausible row.
6. **"Forget that I write Linear tickets with a one-line summary."** The claim is deactivated, retrieval is re-run to *prove* it no longer returns, and the dictation it came from is left untouched.

---

## Architecture

```
  React + Vite  (Desk · Hey Kivi · What Kivi knows · "why did Kivi say this?")
        │  REST
  FastAPI — modular monolith
        ├── ingest/      import · field mapping · chunk · embed
        ├── memory/      sensitive fence → extractor → POLICY → store
        ├── retrieval/   4 channels → RRF → rerank → RAG orchestrator
        ├── reasoning/   abstention gate · citation verifier
        ├── tools/       the 6 Hey Kivi tools
        └── providers/   LLM · embeddings · ASR, all swappable by env var
        │
  PostgreSQL 16 + pgvector   (HNSW cosine + GIN full-text)
```

### The two layers that matter

**Capture is not commitment.** `interactions` / `interaction_chunks` / `episodes` hold everything ever dictated, verbatim and searchable forever. `memories` holds a small set of typed, evidence-backed claims. Most dictations produce none, and that is the expected outcome.

**A memory is an object, not an embedding.** Every memory carries `type`, `claim`, `scope`, `explicitness`, `confidence`, `corroboration_count`, `status`, temporal state, supersede links, and one or more `memory_evidence` rows pinning it to an exact character span in a source dictation.

### RAG — hybrid, dual-corpus, memory-augmented

Five stages, in [`backend/app/retrieval/rag.py`](backend/app/retrieval/rag.py):

1. **Understand** — intent, entities, app, and *"last Thursday afternoon"* resolved to a real UTC window.
2. **Retrieve** across two corpora on four channels — **semantic** (pgvector cosine over memory claims *and* transcript chunks), **lexical** (Postgres full-text, which catches "Acme" and "Halo" where dense vectors blur proper nouns), **structured** (hard SQL filters on time/app/project), **episodic** (time-anchored).
3. **Fuse** with Reciprocal Rank Fusion, then rerank deterministically:
   `final = RRF + 0.50·similarity + 0.25·confidence + 0.15·recency + 0.20·scope − 0.30·inactive − 0.35·superseded_source`
4. **Augment** — only retrieved material reaches the model, labelled `[M…]` and `[E…]`, with memories capped so transcripts keep part of the window.
5. **Verify** — every citation must resolve to something actually retrieved, or the answer is downgraded from `grounded` to `tentative`.

**Abstention is a gate, not an instruction.** If nothing clears both the relevance floor and the score threshold, the generator is never called. Refusing to invent is a property of the control flow, not a request in a prompt.

### The policy engine

The model *proposes*; seven ordered rules in plain Python *decide*, each with a stable id that appears in the trace and in the UI:

`R1_SENSITIVE_FENCE` · `R2_EPHEMERAL` · `R3_INFERENCE_FLOOR` · `R4_DUPLICATE` · `R5_CONTRADICTION` · `R6_SCOPE_SPLIT` · `R7_REMEMBER`

Confidence is a published formula, never a model's self-report, and never 1.0 — see [`DECISIONS.md` D-14](DECISIONS.md).

---

## Results

`mock` provider, `bge-small-en-v1.5` embeddings, 520 records. Full report in [`evaluation/results/`](evaluation/results/).

| | |
|---|---:|
| **Evaluation cases passed** | **48 / 48 (100%)** |
| Interactions ingested | 520 |
| Memories created (total / active) | 15 / 12 |
| Memories per 100 interactions | 2.88 |
| Noise → memory rate | 0.00% |
| Fence integrity | 100% (14 fenced, 0 leaked) |
| Evidence support rate | 100% |
| Abstention accuracy | 100% |
| Useful memory precision | 86.67% |
| Query latency p50 / p95 | 59 ms / 70 ms |
| Retrieval latency p50 | 18 ms |
| Memory correction latency p50 | 47 ms |
| Ingest wall time (520 records) | 17.8 s |
| Database growth | 4.68 MB (≈9.4 KB per interaction) |

All fifteen evaluation classes A–O pass: memory creation, useful retention, deliberate ignoring, duplicates, contradictions, outdated memory, scope isolation, multi-hop retrieval, unsupported questions, hallucination resistance, correction, forgetting, preference application, evidence attribution, and live-instruction override.

**Honesty about these numbers.** They were produced with `LLM_PROVIDER=mock` — a deterministic rule-based stand-in, not a language model. It does real extraction and composition over real retrieved state and has no knowledge of the evaluation set, but it is weaker than `sarvam-m` at paraphrase and nuance. Every report states which provider produced it. Cost reads `$0.0000` because `mock` makes no API calls; run with `LLM_PROVIDER=sarvam` for real token and cost figures.

---

## Model stack

| Layer | Default | Alternatives |
|---|---|---|
| LLM | `mock` — deterministic, zero credentials | `sarvam` (**sarvam-m**, open-weight, Qwen3-derived) · `openai_compatible` (**Qwen3-8B** via vLLM / Ollama / LM Studio) |
| Embeddings | `fastembed` — **bge-small-en-v1.5**, 384-d, CPU | `qwen3` (**Qwen3-Embedding-0.6B**, 1024-d) · `hashing` (offline fallback) |
| ASR | `replay` — transcripts only | `indicconformer` / `whisper` adapters declared, not wired |

One interface each; swapping is one environment variable. The brief permits replaying transcripts, so **no speech recognition is implemented** — a mandatory microphone is the easiest way to break a one-shot review.

---

## Limitations

Stated plainly, because a system that hides these is not inspectable.

- **`mock` is not a language model.** It handles the demo corpus well and generalises worse. Paraphrase-heavy questions on an unseen corpus will do better with `LLM_PROVIDER=sarvam`.
- **The sensitivity fence is lexical.** It catches the vocabulary of health, money, conflict and distress. Oblique phrasing will get through. The design compensates by fencing at two points (extraction *and* retrieval), but it is pattern matching, not comprehension.
- **One user.** Single-tenant by design; there is no auth.
- **No real ASR, no live audio.** By choice, per the brief.
- **Contradiction detection is pairwise** within a type and scope. A claim contradicted by the *combination* of two others is not caught.
- **Commitments expire on a parsed due date**, and date parsing is limited to weekdays and simple phrases.
- **The corpus is synthetic.** It was written to exercise the system, including its failure modes, and the system was tuned against it — the reviewer's corpus is the honest test.
- **`useful_memory_precision` is measured against the evaluation's own query set**, so it says how much of memory that set reached, not how much a real user would.

---

## AI use

This repository was built with AI assistance (Claude), used for implementation, test authoring and documentation drafting.

**Part One was not.** The positioning statement, vision document and survey analysis in [`part-one/`](part-one/) and [`research/`](research/) are the author's own work, as the brief requires — the 100-response survey was designed, run and analysed by hand, and every claim in those documents traces to it via [`part-one/evidence-basis.md`](part-one/evidence-basis.md).

Part Two's product decisions follow from that position; `DECISIONS.md` records where each one came from. The synthetic corpus was generated by a deterministic script, not by a language model.

---

## Repository map

```
part-one/       positioning statement, vision document, evidence basis   (Part One)
research/       the n=100 survey: raw CSV + analysis
DECISIONS.md    29 logged build decisions, with what was rejected and why
RUN.md          the primary review method, step by step
backend/        FastAPI app, memory engine, RAG layer, migrations, CLI
frontend/       React + Vite client
corpus/         generator, 520-record corpus (JSONL + CSV), manifest, field mapping
evaluation/     assertions, harness, generated results
docs/           architecture notes
```
