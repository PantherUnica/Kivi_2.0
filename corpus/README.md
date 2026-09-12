# Corpus

`kivi_corpus.jsonl` (and the identical `.csv`) is the development corpus:
520 dictation records for one synthetic professional across a coherent
10-week arc. Regenerate deterministically with:

    python corpus/generate_corpus.py

`manifest.json` lists the planted cases and maps each label to the record id
it became. The evaluation harness asserts against those labels.

## Record shape

| field | meaning |
|---|---|
| `id` | stable record id; import is idempotent on it |
| `captured_at` | ISO-8601 UTC |
| `app` | Slack / Gmail / Notion / Linear / WhatsApp |
| `destination` | channel, recipient or page |
| `project` / `client` | Halo/Acme, Atlas/Northwind, Ember/Vertex |
| `style` | the Kivi Style in force at dictation time |
| `raw_asr` | degraded recogniser output - lowercased, unpunctuated, with homophone slips |
| `formatted_text` | what Kivi wrote |
| `duration_ms` | |
| `mode` | `dictation` |
| `plant` | evaluation label, or null for noise. Ignored by the product; read only by the harness |

`raw_asr` genuinely differs from `formatted_text` in all 520 records - for
example `"the hello handoff is ready"` for `"the Halo handoff is ready"` - so
the two fields exercise different code paths rather than being copies.

## Importing your own

See RUN.md section 9. Common field names are recognised automatically; if
yours differ, copy `mapping.example.json` and pass it with `--mapping`.
