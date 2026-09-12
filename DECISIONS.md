# DECISIONS.md

A running log of every major decision taken while building Kivi's semantic memory system (Golden Goose, Part Two). Each entry records **what** was decided, **why**, what was **rejected**, and what it **costs**.

Part One (`part-one/positioning-statement.md`, `part-one/vision-document.md`) is the strategic foundation. Nothing here contradicts it; where a decision traces to Part One or to the n=100 survey (`research/kivi_survey_analysis.md`), the link is stated.

---

## Phase 0 — Position to product

### D-01 | Scope | Kivi remembers *what you said* and *how you say it* — never *who you are*

**Decision.** Semantic memory is limited to two families: **episodes** (what happened, when, to which destination, what was decided or promised) and **communication patterns** (how this person writes, scoped by destination and audience).

**Why.** Straight from the positioning statement. The survey backs it hard: working style and tone was the single highest-agreement item in the dataset (81% of students, 87% of professionals ranked it top-2), while *people & relationships* scored 25% top-2 and was ranked **dead last by 43%** — the sharpest outlier in the survey, rejected by both segments.

**Rejected.** A people/relationship graph; personality or trait modelling; sentiment or mood tracking; any "user profile" object.

**Cost.** Kivi cannot answer "who is Priya to me?" It answers "what did you say about Priya's scope doc, and when?" That is the intended trade.

---

### D-02 | Architecture | A transcript is an *event*. A memory is a *claim*. Capture is not commitment

**Decision.** Two separate layers with separate tables. Every imported interaction is stored verbatim and is fully searchable forever (`interactions`, `interaction_chunks`, `episodes`). Only a small minority are ever distilled into `memories`.

**Why.** The brief evaluates "what it deliberately ignored" as its own axis. If everything becomes a memory, that question has no answer. The competitor dossier reached the same conclusion independently: capture does not equal commitment.

**Rejected.** Treating every transcript chunk as a memory — the naive-RAG position.

**Cost.** Two retrieval corpora instead of one, which the RAG layer must fuse (see D-16).

---

### D-03 | Memory model | An embedding is not a memory. A memory is a typed, evidence-backed object

**Decision.** A memory is a row carrying `type`, `claim` (human-readable), `scope`, `explicitness`, `confidence`, `corroboration_count`, `status`, `sensitivity`, `action_policy`, temporal state, supersede links, **and** one or more `memory_evidence` rows holding the exact quoted span with character offsets back into a source interaction. The embedding is one retrieval index among four — never the source of truth.

**Why.** The brief requires inspecting the memory created/retrieved/changed/rejected, its provenance, the resulting behaviour, and the reason. A vector-only store cannot answer any of those four.

**Cost.** Extraction is an LLM call per interaction rather than a free embedding.

---

### D-04 | Memory types | Exactly five, each earned from the survey ranking

`style_rule` (survey #1, 81/87%) - `decision` (#2, 75%) - `routine` (#3, 65%) - `commitment` (#4, 62% students / **80%** professionals) - `project_context` (#5, 59%).

**Why.** Build order should follow what people actually ranked useful. Anything outside these five needs a survey number to justify its existence, and none had one.

**Rejected.** `person`, `trait`, `mood`, `health`, `finance` as memory types. These exist in the code **only as categories the system recognises in order to refuse them** (D-06).

---

### D-05 | Provenance | Four grades, and inference can never become fact on its own

`explicit_statement` (0.70) - `observed_repetition` (0.50, promotes on corroboration) - `inferred` (0.30, **capped at `provisional`, can never reach `active` without a user confirmation**) - `user_correction` (0.95, supersedes everything in its scope).

**Why.** Vision document: "Kivi must never convert an inference into a stored fact." Culnan & Armstrong (1999): explicit disclosure reads as fair exchange, inference reads as surveillance. This is that principle as an enforced state machine, not a prompt request.

**Cost.** Kivi learns more slowly than a system that trusts its own inferences.

---

### D-06 | Privacy | Sensitive categories are fenced from *derivation*, not from the record

**Decision.** Relationship conflicts, emotional/mental state, financial situation, and health/medical details are detected and **excluded from memory extraction entirely**. The refusal is logged to `ignored_spans` as category + rule + hash, never the sensitive text itself. The original interaction is still stored verbatim, because it is the user's own transcript and the reviewer supplies raw ASR.

**Why.** Survey: 67% want relationship conflicts never auto-captured, 51% emotional state, 44% finances, 42% health. The vision document commits to building to the stricter **professional** threshold (87% / 67%) rather than the softer aggregate. The doctor's-appointment scenario split 42% "keep only the work part" / 37% "depends", which supports fencing *derivation* rather than destroying the record.

**Escape hatch.** A literal "remember this" marker upgrades a fenced span to `ASK_CONFIRMATION`, never to silent capture. 54% said sensitive work topics are fine "only if I say remember this".

---

## Phase 1 — Technical decisions

### D-07 | Models | Hosted open-weight by default, local open-weight documented, mock for zero-key

**Decision.** One `LLMProvider` interface, three implementations selected by `LLM_PROVIDER`: `sarvam` (default, `sarvam-m`, open-weight, Qwen3-derived), `openai_compatible` (Qwen3-8B via vLLM / Ollama / LM Studio, set `LLM_BASE_URL`), `mock` (deterministic, no credentials).

**Why.** The brief's reviewer is a coding agent on unknown hardware that "will not repair the application". The development machine is a Ryzen 5 8540U with 16 GB RAM and no CUDA: Qwen3-8B runs there at roughly 3-6 tok/s, which is hours for a 500-record ingest. Hosting the default keeps the open-weight commitment while making the review path survivable.

**Rejected.** Mandating a local 8B (fails on reviewer hardware); defaulting to a closed frontier model (abandons the open-weight position).

**Cost.** An API key for the best results. Mitigated by `mock`, below.

---

### D-08 | Models | The `mock` provider is a real deterministic extractor, not canned answers

**Decision.** `mock` performs genuine rule-based candidate extraction and answer composition over the **real** retrieved state. It never returns a pre-written answer for a pre-written question, and it has no knowledge of the evaluation set.

**Why.** A zero-credential path means the reviewer can run migrate, import, eval and the UI with nothing but Docker, and still see real memory decisions. Non-negotiable rule from the brief: no hardcoded demo answers, no scripted retrieval.

**Honesty note.** `mock` is weaker than `sarvam` at paraphrase and nuance. Every evaluation report states which provider produced it.

---

### D-09 | Embeddings | `bge-small-en-v1.5` (384-d) local on CPU, with a no-network fallback

**Decision.** Default `EMBEDDING_PROVIDER=fastembed` (BAAI/bge-small-en-v1.5, ~130 MB, 384-dim, CPU, cached in a Docker volume). `qwen3` (Qwen3-Embedding-0.6B, 1024-dim) is documented as an upgrade. `hashing` is a pure-NumPy deterministic fallback used automatically if no model can be fetched, and it says so, loudly, in the logs and in the evaluation report.

**Why.** Embeddings sit in the hottest path and should not require an API. Qwen3-0.6B is viable but a 1.2 GB first-run download is a real risk for a one-shot reviewer; bge-small gets most of the quality at a tenth of the weight.

**Cost.** Changing embedding model changes the pgvector column dimension, so `EMBED_DIM` is read at migration time and switching requires `make reset`. Documented in RUN.md.

---

### D-10 | ASR | Interface plus transcript replay. No speech recognition is built

**Decision.** `ASRProvider` exists with `replay` (default), and documented adapters for `indicconformer` and `whisper`.

**Why.** The brief explicitly permits replaying transcripts and says not to build ASR. A mandatory microphone is the single easiest way to make the review path fail.

---

### D-11 | Review path | Full Docker Compose (db + api + web) is the primary method

**Decision.** `docker compose up` starts Postgres 16 + pgvector, the FastAPI backend, and the web client. The backend image pins Python 3.12.

**Why.** The development machine runs Python 3.14, where several ML wheels do not yet exist. Containerising removes every local toolchain assumption from the reviewer's path and makes `make reset` exact.

---

### D-12 | Stack | Modular monolith. FastAPI plus Vite/React. No microservices

**Decision.** One Python service with clear internal module boundaries (`ingest / memory / retrieval / reasoning / tools / providers / trace`), one React client.

**Why.** The brief asks for a system "narrow enough to finish and complete enough to interrogate". Vite over Next.js purely for build reliability and speed in a container: the evaluation is on the experience, not the framework.

---

## Phase 2 — Memory engine

### D-13 | Decisioning | The LLM proposes; deterministic ordered rules decide

**Decision.** Extraction is a model call producing strictly-validated JSON candidates. The **policy engine is plain Python**: seven ordered rules, each with a stable id that appears in the trace.

| Rule | Fires when | Outcome |
|---|---|---|
| `R1_SENSITIVE_FENCE` | candidate touches a fenced category | IGNORE, or ASK on explicit "remember this" |
| `R2_EPHEMERAL` | temporality is ephemeral / one-off | IGNORE |
| `R3_INFERENCE_FLOOR` | `inferred` with fewer than 2 corroborations | PROVISIONAL, never active |
| `R4_DUPLICATE` | cosine >= 0.88, same type and scope | UPDATE, corroborate and raise confidence |
| `R5_CONTRADICTION` | same type and scope, opposed claim | SUPERSEDE if newer and explicit, else ASK |
| `R6_SCOPE_SPLIT` | same claim, different destination | new scoped memory, never a global one |
| `R7_REMEMBER` | default | CREATE |

**Why.** "Why did Kivi remember this?" must have a stable, auditable answer that does not change with model temperature. A model deciding its own retention policy is not inspectable.

---

### D-14 | Confidence | A published formula, never a model's self-report, never 1.0

`conf = base(explicitness) + 0.10 * min(corroborations, 2.5) - decay(type, age) + 0.15 * user_confirmed`, clamped to `[0.05, 0.98]`.

**Why.** Lee and See (2004): the goal is *appropriate reliance*, not maximum trust. A system that reports 1.0 invites automation bias. Survey open-text, unprompted: *"When it answers confidently with incorrect information... it should mention a probability instead."*

---

### D-15 | Lifecycle | Nothing is hard-deleted; `forgotten` is a state with a timestamp

**Decision.** States: `provisional` to `active` to one of `stale | superseded | contradicted | forgotten | expired`. Forgetting sets `status='forgotten'`, nulls the embedding, and writes a `memory_events` row. Retrieval excludes it and **records the exclusion with a reason**.

**Why.** It makes Memory Correction Latency measurable, and it makes the forget provable rather than asserted. 78% of the survey named easy deletion their top trust requirement: a delete you cannot verify is not a delete.

---

## Phase 3 — Retrieval / RAG

### D-16 | RAG | Kivi is a RAG system over the user's own history: hybrid, dual-corpus, memory-augmented

**Decision.** The answer path is explicitly Retrieval-Augmented Generation, implemented in `backend/app/retrieval/rag.py`, with five stages.

1. **Query understanding.** One structured call: intent, entities, project, app/destination, and *resolved absolute time window* ("last Thursday afternoon" becomes a real UTC range).
2. **Multi-channel retrieval over two corpora.** *Semantic* (pgvector cosine over memory claims **and** interaction chunks), *lexical* (Postgres full-text `tsvector`, which catches proper nouns like "Acme" and "Halo" that dense vectors blur), *structured* (hard SQL filters from stage 1), and *episodic* (time-anchored `episodes`).
3. **Fusion and rerank.** Reciprocal Rank Fusion across channels, then a deterministic rescore: `final = RRF + 0.25*confidence + 0.15*recency_decay(type) + 0.20*scope_match - 0.30*(status != active)`. Every candidate's score breakdown is persisted to `retrieval_traces`.
4. **Augmented generation.** The model sees *only* retrieved context, labelled `[M...]` for memories and `[E...]` for episodes and interactions, and must cite.
5. **Post-verification.** Every citation id is checked against what was actually retrieved; claim-bearing sentences without a citation downgrade the answer from `grounded` to `tentative`.

**Why hybrid RAG and not just vector search.** Pure dense retrieval fails on exactly the queries this product exists for: proper nouns, "last Thursday afternoon", and facts split across three dictations. Hybrid plus structured filtering is what makes those answerable.

**Rejected.** Vector-only RAG; naive chunk-stuffing; retrieving over memories alone (loses episodic recall) or over transcripts alone (loses distilled understanding).

---

### D-17 | RAG | Abstention is a gate *before* generation, not an instruction *inside* it

**Decision.** If no candidate clears the support threshold, the model is never asked to compose a substantive answer. The system returns a templated "that isn't in your history" with an offer to search instead.

**Why.** The brief tests "whether it refuses to invent an answer when the history does not contain one". Prompting a model to abstain is a hope; not calling it is a guarantee.

---

### D-18 | RAG | The live instruction always outranks stored memory

**Decision.** When the current utterance conflicts with a retrieved memory, the memory is retrieved, **shown as overridden**, and not applied.

**Why.** Non-negotiable rule from the brief. Also the single fastest way to destroy trust: stale memory winning an argument with a live instruction.

---

## Phase 4 — Product surface

### D-19 | Boundary | Semantic memory never rewrites dictation. It only writes a quiet note

**Decision.** In regular dictation, memory has exactly one job: capture, distil, and if something was learned, surface a small dismissible note ("Learned: you brief Acme in short paragraphs, from 3 dictations"). It never alters the dictated text. Styles and phonetic memory already own the word level.

**Why.** The brief asks for a coherent boundary between the two modes. This is it: memory is **write-side silent, read-side loud**. The moment memory silently edits what you said, dictation itself becomes untrustworthy.

---

### D-20 | Boundary | Hey Kivi *auto-applies* destination-scoped style on rewrite, always attributed

**Decision.** Any restyle or draft applies the matching `style_rule` automatically, and always states which rule and how many sources produced it. The live instruction overrides.

**Why.** This is the beat that proves memory changes behaviour. Attribution is what keeps it from feeling like surveillance: the survey's #1 professional trust ask was "clearly show me what's stored" (87%).

---

### D-21 | Control | Memory management is conversational. There is no admin panel

**Decision.** "Forget that I prefer concise slides" and "Actually I use bullets with Acme now" are the primary controls. The *What Kivi Knows* surface is editorial field notes, not a CRUD table, and each memory carries an inline one-line correct/forget.

**Why.** Deci and Ryan (2000): autonomy is a need, administration is a chore. The brief: "the person remains in control without becoming the administrator of the system."

---

### D-22 | Trust | "Kivi chose not to remember" is a *product surface*, not a debug log

**Decision.** The ignored log is rendered in the UI: category and date only, never the content.

**Why.** The brief evaluates "what it deliberately ignored". Survey: the top two trust asks in both questions were procedural, not technical. Visible restraint is the cheapest, highest-leverage trust feature in the build.

---

## Phase 5 — Evaluation

### D-23 | Evaluation | Assert on *ingestion* as well as on *answers*

**Decision.** Two phases in one command. Phase A asserts per corpus record what must be remembered, ignored, updated, superseded or left provisional. Phase B asserts retrieval and answer behaviour, including **forbidden strings** for hallucination probes.

**Why.** A query-only evaluation cannot show that the system ignored the right things, which is half of what is being examined.

---

### D-24 | Evaluation | Failures print in full. Aggregates never hide them

**Decision.** `report.md` carries a FAILURES section with the full trace of every failing case; `results.jsonl` carries every case's query, retrieved set, evidence, decision, answer, confidence, support status, latency, tokens and cost.

**Why.** The brief inspects "whether failures remain visible, and whether the conclusions follow from the evidence."

---

### D-25 | Corpus | One synthetic professional, a coherent 10-week arc, planted test structure

**Decision.** ~520 records for a single consultant persona across Slack, Gmail, Notion, Linear and WhatsApp, generated deterministically from a seed. Deliberately planted: corroboration chains, a reversed decision, dated commitments, near-duplicates, an explicit correction, facts split across three dictations, fenced sensitive mentions, ephemeral statements, pure-noise records, and inference bait.

**Why.** Professional, not student, because the survey's sharpest divergence is deadlines and commitments (80% professionals vs 59% students), and because multiple clients are what make *scoped* style memory provable rather than theoretical.

---

## Phase 6 — Decisions forced by the evaluation

These were not planned. The evaluation harness found them, which is the point
of building it before the interface.

### D-26 | Privacy | Stopping *derivation* was not enough. Retrieval needed its own fence

**Found by.** Eval cases Q11 and Q12. Asked *"what do you remember about my
salary?"*, Kivi answered with the sentence containing the salary remark. The
fence (D-06) had correctly refused to build a *memory* from it — but the raw
transcript was still in the retrieval corpus, so RAG quoted it straight back.
The refusal was real and the leak was real at the same time.

**Decision.** `interactions.safe_text` (migration `0002`) holds the text with
fenced sentences removed. It is the **only** text the retrieval corpus is built
from, the only text an answer may quote, and the only text `search_history`
returns. `raw_asr` and `formatted_text` remain verbatim, because the record
belongs to the user and the reviewer supplies raw ASR.

**The principle this sharpens.** A fence on what the system *learns* is not a
fence on what it *says*. Both are needed, and only the second one is visible to
the person asking.

---

### D-27 | RAG | Relevance to the question outranks confidence in the memory

**Found by.** Eval cases Q07, Q09, Q16, Q18. Asked for things the corpus never
contained (*"what is Priya's manager called?"*), Kivi answered `grounded` using
a high-confidence but unrelated memory. The fused score was dominated by
`confidence` and `recency`, both properties of the memory alone, so a
well-established fact about Acme's writing style outranked actual relevance.

**Decision.** Two changes:

1. `RAG_W_SIMILARITY` (0.50) makes query-similarity the largest single term in
   the rerank — larger than confidence (0.25).
2. `apply_threshold` now enforces **two independent bars**: a candidate must be
   *relevant* (dense similarity above `RAG_MIN_SIMILARITY`, or a lexical match)
   **and** clear the fused-score threshold. Failing either excludes it, with the
   reason recorded in `retrieval_traces`.

**Why it matters.** "How sure am I about this memory" and "does this memory
answer the question" are different quantities. Conflating them is precisely how
a memory system becomes confidently irrelevant.

---

### D-28 | RAG | Memories are capped in the context window so transcripts still fit

**Found by.** Eval case Q06, the multi-hop budget question. Distilled memories
almost always outscore raw transcripts, so an unbounded top-k filled the entire
context with memories — and the answer, which was spread across three
dictations that were deliberately never distilled, became unreachable.

**Decision.** `MAX_MEMORIES_IN_CONTEXT = 7`. Transcripts always keep part of the
window. The dual-corpus design (D-02) is worthless if one corpus can crowd the
other out.

---

### D-29 | Forgetting | Forgetting a memory does not delete the dictation

**Found by.** Eval case M02, which originally asserted that after *"forget that
I write Linear tickets with a one-line summary"*, the phrase "acceptance
criteria" must never appear again. It still appeared — from the original
transcript.

**Decision.** That assertion was wrong, and it was the assertion that changed.
Forgetting removes the **claim** and stops retrieval returning it; it does not
erase the user's own words. The corrected assertion is *"no citation to the
forgotten memory survives"*, which is what a person actually means by "stop
using that".

**Why not delete the transcript too.** Deleting a dictation because one claim
was derived from it would destroy unrelated evidence and silently rewrite the
record. Deletion of source material should be an explicit, separate act.

---

## Phase 7 — Landing page

### D-30 | Identity | A creative-studio landing page, borrowing the grammar and not the look

**Decision.** The web client now opens on a landing page (`frontend/src/components/Landing.jsx`) before the product. Its structure borrows from the reference studio site the brief pointed at: bracket-notation eyebrows (`[ like this ]`), a numbered 01–05 list, a looping marquee, a mission statement, a big closing ask, and a footer that echoes the brand idea. Its *look* stays Kivi's own — near-black, one acid green, an editorial serif — because the reference is a light, photography-first site and Kivi has no photography; it has a bird and a position.

**Why.** The brief evaluates "interface, interactions, language, visual character" together, and asked for a premium creative feel rather than an AI dashboard. The five numbered items are the five memory types in the survey's own ranking, so the page argues the position rather than decorating it.

**The animation.** The bird performs the product in one motion: it walks in, stops, tilts its head and listens (rings pulse off the beak), and only then is *"Kivi writes."* typed out. The order matters — writing waits for listening.

**Downloaded for it.** `gsap` 3.12 (ScrollTrigger reveals) and `lenis` 1.1 (smooth scroll), both pinned in `frontend/package.json` and installed at image build. The bird is an original stroked SVG, not an asset; nothing from the reference site is used.

**Live, not copy.** The numbers on the page are fetched from `/api/stats` — dictations kept, things remembered, times it declined — and the page says so.

**Deep links.** `#app`, `#ask`, `#knows` skip the landing so RUN.md's review path is unchanged.
