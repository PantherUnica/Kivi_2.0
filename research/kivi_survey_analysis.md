# Kivi Memory Survey — Analysis

Source data: `kivi_survey_responses.csv` (n=100, collected 6–8 Sep 2026 via the "Kivi" Google Form). This document is the full write-up; the CSV is the raw export it's built from — re-run the numbers against it any time the form gets new responses.

## Snapshot

- **96%** have already been surprised by an AI remembering something from an earlier chat.
- Only **20.4%** are "very comfortable" sharing personal data with AI — everyone else attaches conditions.
- **77.6%** want to delete or edit stored data easily — the single most-requested control.
- **80.6%** rank working style & tone their most-wanted work memory, ahead of project/people context.

## Who responded

- Age: 20–24 (62%), 24–32 (28%), 16–20 (8%), 32–39/40+ (1% each — too small to trend alone).
- Work situation: Student (84%), Working professional/IC (14%), Manager/lead (1%), Not currently working (1%).
- Read every number below against this shape — it's a young, student-heavy, AI-fluent sample.

## The AI landscape today

- Primary AI tool: ChatGPT 48%, Claude 28%, Gemini 14%, Siri/Assistant/Alexa 3%, None regularly 3%, other/multiple ~4%.
- App-switching: 51% "constantly" switch tools/apps, 38% "a few times a day."
- Voice dictation use: 35% rarely, 25% a few times/week, 21% daily, 19% never — voice is already a live surface for most of this audience, not a novelty.

## Trust & comfort

Overall comfort sharing personal data with AI (98–100 responses): Very comfortable 20%, Somewhat comfortable 28%, Only certain things 28%, Not very comfortable 18%, Not comfortable at all 6%. The middle two "conditional" categories dominate — comfort is not binary.

**What would make AI tools more trustworthy?** (select all that apply)
1. Let me delete/edit stored data easily — 78%
2. Never share my data with other companies — 73%
3. Clearly show me what's stored — 71%
4. Ask permission before saving something new — 60%
5. Store on-device, not cloud — 43%
6. Explain why it used something — 30%
7. Nothing, won't fully trust it — 6%

**What would ease comfort with personal memory specifically:** Delete instantly (65%), Never used for ads/marketing (64%), Only what I explicitly shared, never inferred (52%), Full visibility into everything stored (47%).

The top two asks in both questions are procedural, not technical — a visible, revocable memory beats a smarter model.

## Memory preferences — work vs. personal

Ranked 1 (most useful) to 5 (least). Figures below are % who ranked the item in their **top 2**.

**Work memory:**
1. Working style & tone — 81%
2. Past decisions & discussions — 75%
3. Recurring tasks/routines — 65%
4. Deadlines & commitments — 62%
5. Project & people context — 59%

**Personal memory:**
1. Personal goals & plans — 77%
2. Habits & routines / Preferences — ~55–59% (statistical tie)
3. Recurring personal commitments — 55%
4. People & relationships — **25%** (and ranked dead last by 43% of respondents — the sharpest outlier in the survey)

## Sensitive-topic boundaries

- OK remembering sensitive **work** topics (performance feedback, salary, conflicts): No 17%, Only if I say "remember this" 54%, Yes if it helps 29%.
- OK remembering sensitive **personal** moments (an argument, a health scare): No 39%, Only if explicit 42%, Yes if it helps 13%.
- **Should never be auto-captured, even mentioned in passing:** Relationship conflicts (67%), Emotional/mental state (51%), Financial situation (44%), Health/medical details (42%). 24% are fine with all of it.
- The doctor's-appointment scenario (personal remark inside a work dictation): 42% want only the work part kept, 37% say "depends," 10% want everything kept together.

## Segment cuts: Students vs. Working professionals

n=84 students, n=15 professionals (IC + manager/lead) — professional segment is small, read as directional.

| | Students | Professionals | Read |
|---|---|---|---|
| "Very comfortable" sharing data | 21% | 13% | Professionals more guarded |
| Top trust ask | Delete/edit easily (79%) | **See what's stored (87%)** | Different #1 priority |
| Exclude relationship conflicts | 66% | **87%** | Professionals fence this harder |
| Exclude financial situation | 41% | **67%** | Same pattern |
| Sensitive work topics — hard "no" | 14% | **33%** | Professionals 2.3x more likely to refuse outright |
| Work memory: deadlines & commitments | 59% (lowest of 5) | **80%** (2nd highest) | Sharpest divergence in the dataset |
| Work memory: working style/tone | 81% | 87% | Both segments' universal #1 |
| Personal: goals/plans | 76% | 80% | Both segments' #1 personal ask |
| Personal: people & relationships | 24% (last) | 33% (still last) | Rejected by both, gap narrows for pros |
| Primary AI tool | ChatGPT (52%) | **Claude (53%)** | Pros already lean toward the more cautious tool |
| Reset preference | Keep everything (57%) | Let it figure out relevance (40%) | Pros want adaptive, not manual |
| Voice dictation — "Never" | 17% | **33%** | Pros adopt voice input less in this sample |

Claude's own users are the most privacy-cautious segment of any AI tool in the survey (41% "only certain things," just 11% "very comfortable" — vs. ChatGPT users at 32% "very comfortable"). Professionals lean Claude, so the two cautious signals compound.

## In their words (themes from open text)

- **Wants tone & style remembered, not re-explained:** "My style of work and the tone and importance I give for particular things." / "It should not be stolen and applied elsewhere — should remain for me."
- **Wants project context carried forward:** "I'm preparing for a job switch... assume I want an interview-focused, practical answer, not just a textbook explanation."
- **What breaks trust:** "The more I share, the more my uniqueness decreases — it learns from it and applies the same to everyone." / "When it answers confidently with incorrect information... it should mention a probability instead."
- **When memory helped:** "I was finishing an assignment where I had to bring in a previous report which I'd completely forgotten — the assistant brought it back and saved me."
- **When memory failed:** "I uploaded one PDF, then another, and asked for a summary — it summarised the older one instead of the one I'd just added."
- **A minority counter-signal (n=1, new response):** wants Kivi to remember "the words and things that happened with a specific person" — a real want, but a minority one (matches the 25–33% who *do* rank relationships useful) — argues for opt-in per-person memory, not a hard ban.

## Most common things Kivi should have (consensus across both segments)

1. A visible **"what Kivi remembers about me"** screen — both segments' top-2 trust ask, ship early.
2. **One-sentence delete/edit** ("forget that I said X") — both above 73%.
3. **Ask permission before saving a new memory type** — both above 58%.
4. **Working style & tone as the flagship memory** — highest-agreement item in the whole survey (81–87%).
5. **Past decisions/deadlines & commitments remembered** — weight deadlines heavily for professional-facing use cases.
6. **Personal goals & plans as the flagship personal-memory type** (76–80%).
7. **Hard-fence relationship conflicts, financial situation, health, and emotional state** from automatic capture — default to the stricter (professional) threshold.
8. **"People & relationships" opt-in only, never default-on** — the one thing both segments actively reject as an automatic default.
9. **Never share memory data with third parties / no ad use** — near-universal (63–80%).
10. **On-device/local storage as an option** — 42–47% across both segments; real enough to architect for.
