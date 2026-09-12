# Architecture notes

Companion to `README.md`. This is the level of detail an engineer needs to
change the system rather than just run it. `DECISIONS.md` says *why*.

## Request paths

### Dictation (write-side)

```
POST /api/dictation
  -> Interaction row            verbatim: raw_asr + formatted_text
  -> sensitive.redact()         safe_text = text minus fenced sentences
  -> chunk(safe_text) + embed   the episodic retrieval corpus
  -> Episode row                one cheap summary
  -> process_interaction()      the memory pipeline, below
  <- { text (UNCHANGED), learned: {...}, note }
```

The response returns the dictated text byte-for-byte. Memory never edits it
(D-19). `learned` drives the quiet note on the Desk.

### Hey Kivi (read-side)

```
POST /api/hey-kivi/ask
  -> rag.understand()           intent, entities, resolved UTC window
  -> orchestrator routes to ONE tool by intent
       recall            -> rag.answer()      the full RAG turn
       search_history    -> episodic lookup
       restyle           -> style memory + attribution
       what_do_you_know  -> grouped memory read
       correct_memory    -> supersede + propagate
       forget_memory     -> deactivate + verify
  -> persist_turn()             HeyKiviTurn + one RetrievalTrace per candidate
```

## The memory pipeline

```
formatted_text
   |
   redact()  ------------------> ignored_spans   (category + hash only)
   |
   safe_text
   |
   extract()  [LLM]  ----------> memory_decisions (R0_MALFORMED for bad JSON)
   |
   candidates (validated, offset-anchored)
   |
   for each: neighbours = same-type memories scored by cosine
   |
   decide()  [pure Python] ----> memory_decisions (rule_id + rationale, ALWAYS)
   |
   +-- REMEMBER / PROVISIONAL / CONTRADICT / ASK -> Memory + MemoryEvidence
   +-- UPDATE                                    -> corroborate, raise confidence
   +-- IGNORE                                    -> nothing stored, decision logged
```

Every branch writes a `memory_decisions` row. A rejected candidate leaves
exactly as much evidence behind as an accepted one.

## Where to change things

| To change | Edit |
|---|---|
| what counts as sensitive | `app/memory/sensitive.py` — `FENCE` |
| what may be remembered | `app/memory/extractor.py` — `EXTRACT_SYSTEM` prompt |
| when it is remembered | `app/memory/policy.py` — the seven rules |
| how confident it is | `app/memory/confidence.py` — `BASE`, `HALF_LIFE_DAYS` |
| how things are found | `app/retrieval/channels.py` — the four channels |
| how they are ranked | `app/retrieval/fusion.py` — the rescore weights |
| when it refuses | `app/retrieval/fusion.py` — `apply_threshold`, plus `RAG_MIN_SIMILARITY` |
| what Hey Kivi can do | `app/tools/registry.py` — six tools |
| which model | `.env` — `LLM_PROVIDER`, nothing else |

## Provider contract

```python
class LLMProvider(Protocol):
    name: str
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult
```

`LLMResult` always carries `model`, `prompt_tokens`, `completion_tokens`,
`latency_ms`, `cost_usd`, so metering happens in one place rather than at each
call site. Prompts carry a `TASK:` marker in the system message, which is how
the deterministic `mock` provider dispatches and how a real model is told what
shape of JSON to return.

Failures are raised, never swallowed into a weaker fallback — a run that
silently degraded would report numbers that mean something other than what
they claim.

## Indexes that matter

| Index | Serves |
|---|---|
| `hnsw (embedding vector_cosine_ops)` on memories / chunks / episodes | the semantic channel |
| `GIN (tsv)` on the same three plus interactions | the lexical channel |
| `(user_id, captured_at)` on interactions | the structured channel's time window |
| `(user_id, type, status)` on memories | policy neighbour lookup |
| partial `(fenced) WHERE fenced = FALSE` | keeps fenced chunks out cheaply |

`tsv` columns are Postgres GENERATED columns, so the full-text index cannot
drift from the text it indexes — there is no trigger to forget to maintain.

## Things deliberately absent

No agent framework, no task queue, no cache layer, no auth, no multi-tenancy,
no streaming. Each would be defensible in production and each would make the
system harder to interrogate in a review, which is what this build is for.
