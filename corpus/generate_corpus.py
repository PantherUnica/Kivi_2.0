#!/usr/bin/env python3
"""
Generate the development corpus (DECISIONS.md D-25).

One synthetic professional, a coherent 10-week arc, ~520 dictation records.
Deterministic: the same seed always produces the same corpus, so evaluation
results are reproducible.

Persona
    Maya Iyer, independent product & design consultant.
    Clients: Acme (project Halo), Northwind (project Atlas), Vertex (project Ember).
    Collaborators: Priya, Dev, Rohan, Sana.
    Apps: Slack, Gmail, Notion, Linear, WhatsApp.

Planted structure (what the evaluation asserts against)
    corroboration chain      a style rule stated once, then observed 4 more times
    reversed decision        Postgres in week 2, DynamoDB in week 6
    dated commitments        with real due dates, so expiry is testable
    near-duplicates          the same rule reworded, to exercise dedup
    multi-hop facts          the Halo budget split across three dictations
    fenced sensitive spans   health / financial / relationship / emotional
    ephemeral statements     true today only
    inference bait           hedged statements that must stay provisional
    pure noise               records that must produce NO memory at all

    usage: python corpus/generate_corpus.py [--out corpus/kivi_corpus.jsonl]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260908
START = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)   # Monday, week 1

APPS = {
    "Slack": ["#halo-build", "#atlas-migration", "#ember-ds", "Priya", "Dev"],
    "Gmail": ["rohan@northwind.com", "priya@acme.com", "team@vertex.io"],
    "Notion": ["Halo spec", "Atlas runbook", "Ember tokens"],
    "Linear": ["HALO", "ATL", "EMB"],
    "WhatsApp": ["Sana", "Dev"],
}

PROJECTS = {"Halo": "Acme", "Atlas": "Northwind", "Ember": "Vertex"}

# Where work on each project actually gets sent, per app. Keeping this honest
# is what makes destination-scoped style memory meaningful rather than noise.
DEST_BY_PROJECT = {
    "Slack": {"Halo": ["#halo-build", "Priya"], "Atlas": ["#atlas-migration", "Dev"],
              "Ember": ["#ember-ds", "Sana"]},
    "Gmail": {"Halo": ["priya@acme.com"], "Atlas": ["rohan@northwind.com"],
              "Ember": ["team@vertex.io"]},
    "Notion": {"Halo": ["Halo spec"], "Atlas": ["Atlas runbook"], "Ember": ["Ember tokens"]},
    "Linear": {"Halo": ["HALO"], "Atlas": ["ATL"], "Ember": ["EMB"]},
    "WhatsApp": {"Halo": ["Dev"], "Atlas": ["Dev"], "Ember": ["Sana"]},
}


def asr_noise(text: str, rng: random.Random) -> str:
    """
    Degrade clean text into plausible raw ASR output.

    Lowercased, punctuation mostly gone, a few homophone slips - which is what
    makes `raw_asr` genuinely different from `formatted_text` rather than a
    copy with a different key.
    """
    swaps = {
        "Acme": "acme", "Halo": "hello", "Atlas": "atlas", "Ember": "amber",
        "Priya": "preeya", "Rohan": "rohan", "Northwind": "north wind",
        "Postgres": "post gres", "DynamoDB": "dynamo d b", "Vertex": "vertex",
        "their": "there", "to": "to", "week": "week",
    }
    out = text
    for src, dst in swaps.items():
        if rng.random() < 0.45:
            out = out.replace(src, dst)
    out = out.lower()
    out = re.sub(r"[,;:]", "", out)
    if rng.random() < 0.7:
        out = out.replace(".", "")
    if rng.random() < 0.25:
        out = re.sub(r"\bi\b", "i", out)
    return re.sub(r"\s+", " ", out).strip()


def record(idx: int, when: datetime, app: str, dest: str, text: str,
           project: str | None, plant: str | None, rng: random.Random) -> dict:
    return {
        "id": f"rec-{idx:04d}",
        "captured_at": when.isoformat(),
        "app": app,
        "destination": dest,
        "project": project,
        "client": PROJECTS.get(project or "", None),
        "style": {"Slack": "casual", "Gmail": "formal", "Linear": "terse",
                  "Notion": "structured", "WhatsApp": "casual"}.get(app, "default"),
        "raw_asr": asr_noise(text, rng),
        "formatted_text": text,
        "duration_ms": max(1200, len(text) * rng.randint(38, 62)),
        "mode": "dictation",
        "plant": plant,
    }


# --------------------------------------------------------------- content banks
NOISE = [
    "Pushed the latest {p} wireframes to the shared folder for review.",
    "Ran through the {p} prototype end to end and captured a few screenshots.",
    "Left comments on the second half of the {p} flow.",
    "Quick note that the {p} staging environment is back up.",
    "Renamed the {p} components so the naming matches the spec.",
    "Exported the {p} icons at two and three x.",
    "Checked the {p} analytics dashboard, nothing unusual this week.",
    "Copied the {p} meeting notes into the shared doc.",
    "Reviewed the pull request for the {p} settings screen.",
    "Cleaned up the old {p} branches that were already merged.",
    "Updated the {p} changelog with this week's items.",
    "Synced the {p} design tokens with the latest build.",
    "Walked through the {p} onboarding copy one more time.",
    "Archived the {p} research recordings from last month.",
    "Fixed the spacing on the {p} summary card.",
]

EPHEMERAL = [
    "Let's use the blue slides for tomorrow's {p} review, just for that one.",
    "I'm working from the cafe today so I might drop off the {p} call.",
    "Going to skip the {p} standup this morning, back tomorrow.",
    "Using the corner room for today's {p} session.",
    "I'll keep this one short, running between {p} meetings right now.",
    "Only for today, route the {p} questions to Dev.",
    "I'm tired today so the {p} review will be brief.",
]

INFERENCE_BAIT = [
    "I think I'm probably more productive on {p} in the mornings.",
    "I guess I tend to prefer shorter {p} reviews, not sure.",
    "Maybe I work better on {p} when there are fewer meetings.",
    "It seems like {p} decisions go faster with Dev in the room, perhaps.",
]

SENSITIVE = [
    ("Sorry, running late, I just left a doctor's appointment. "
     "Anyway, the {p} handoff is ready for review.", "health"),
    ("Between us, the salary discussion with the {p} client went badly. "
     "The design review is still on for Thursday.", "financial"),
    ("I had an argument with Dev about the {p} scope and we're barely speaking. "
     "The tickets are still moving though.", "relationship_conflict"),
    ("Honestly I'm feeling pretty anxious about the {p} deadline. "
     "The spec is written and ready.", "emotional_state"),
    ("My EMI went up this month which is stressful. "
     "Separately, {p} phase two kicks off Monday.", "financial"),
    ("Therapy ran over so I'm late to the {p} sync. Notes are in Notion.", "health"),
    ("Feeling burnt out this week. The {p} deliverable is on track regardless.",
     "emotional_state"),
]

STYLE_ACME = [
    "Remember that for Acme I always keep updates to three short paragraphs, no bullet points.",
    "Writing the Acme update now, keeping it to short paragraphs and no bullets as usual.",
    "Acme update going out, three short paragraphs, no bullet points.",
    "For Acme I want no bullets, just short paragraphs like always.",
    "Drafting the Acme note in short paragraphs, no bullet points.",
]

STYLE_LINEAR = [
    "For Linear tickets I always open with a one-line summary then the acceptance criteria.",
    "Writing this ticket with a one-line summary first, then acceptance criteria.",
    "Linear ticket, one-line summary then acceptance criteria as usual.",
]

STYLE_NORTHWIND = [
    "For Northwind emails I keep it formal and always sign off with Best regards, Maya.",
    "Northwind email drafted formally, signing off with Best regards, Maya.",
]

ROUTINES = [
    "Every Monday I write the weekly status update into Linear before the standup.",
    "Writing the Monday status update into Linear, same as every week.",
]

PROJECT_FACTS = [
    ("Halo is Acme's internal operations tool and I own the design system work on it.",
     "Halo"),
    ("Atlas is the Northwind platform migration and Rohan is the client lead.", "Atlas"),
    ("Ember is the Vertex design system rebuild running through the autumn.", "Ember"),
]

# The multi-hop case: no single dictation carries the total.
BUDGET = [
    "Confirmed that Halo phase one is budgeted at twelve lakh.",
    "Halo phase two adds another eight lakh on top of phase one.",
    "The Halo budget conversation is closed now, nothing further to add this quarter.",
]

COMMITMENTS = [
    ("I'll send Priya the revised Halo scope document by Friday.", "Halo"),
    ("I need to get Rohan the Atlas audit summary by Thursday.", "Atlas"),
    ("I promised Sana the Ember token sheet by Monday.", "Ember"),
    ("I'll deliver the Halo accessibility review by Wednesday.", "Halo"),
]

DECISION_A = "We're going with Postgres over Mongo for the Halo data layer."
DECISION_B = "We're moving Halo off Postgres to DynamoDB after the load testing."


def build(target: int = 520) -> list[dict]:
    rng = random.Random(SEED)
    records: list[dict] = []
    idx = 0

    def at(week: int, day: int, hour: int, minute: int = 0) -> datetime:
        return START + timedelta(weeks=week, days=day, hours=hour - 9, minutes=minute)

    def add(when, app, dest, text, project, plant=None) -> None:
        nonlocal idx
        idx += 1
        records.append(record(idx, when, app, dest, text, project, plant, rng))

    # ---- week 1: project context established --------------------------------
    for i, (fact, proj) in enumerate(PROJECT_FACTS):
        add(at(0, i, 9, 30), "Notion", f"{proj} spec", fact, proj, f"project_context_{proj}")

    add(at(0, 0, 10), "Slack", "#halo-build", STYLE_ACME[0], "Halo", "style_acme_1_explicit")
    add(at(0, 1, 11), "Linear", "HALO", STYLE_LINEAR[0], "Halo", "style_linear_1_explicit")
    add(at(0, 2, 14), "Gmail", "rohan@northwind.com", STYLE_NORTHWIND[0], "Atlas",
        "style_northwind_1_explicit")
    add(at(0, 3, 9), "Linear", "HALO", ROUTINES[0], None, "routine_1_explicit")

    # ---- week 2: the decision that will later be reversed -------------------
    add(at(1, 1, 15), "Slack", "#halo-build", DECISION_A, "Halo", "decision_postgres")
    add(at(1, 2, 10), "Notion", "Halo spec", BUDGET[0], "Halo", "budget_phase_one")

    # ---- corroboration of the Acme style rule, spread across weeks ----------
    for n, variant in enumerate(STYLE_ACME[1:], start=2):
        add(at(1 + n, n % 5, 11, 20), "Slack", "#halo-build", variant, "Halo",
            f"style_acme_{n}_repeat")

    for n, variant in enumerate(STYLE_LINEAR[1:], start=2):
        add(at(2 + n, (n + 1) % 5, 16), "Linear", "HALO", variant, "Halo",
            f"style_linear_{n}_repeat")

    add(at(3, 2, 10), "Gmail", "rohan@northwind.com", STYLE_NORTHWIND[1], "Atlas",
        "style_northwind_2_repeat")
    add(at(4, 0, 9, 15), "Linear", "ATL", ROUTINES[1], None, "routine_2_repeat")

    # ---- commitments, with real due dates -----------------------------------
    for n, (text, proj) in enumerate(COMMITMENTS):
        add(at(2 + n, 1, 13), "Slack", "Priya" if proj == "Halo" else "Dev", text, proj,
            f"commitment_{n + 1}")

    # ---- multi-hop budget, deliberately split -------------------------------
    add(at(3, 3, 14), "Notion", "Halo spec", BUDGET[1], "Halo", "budget_phase_two")
    add(at(7, 1, 11), "Notion", "Halo spec", BUDGET[2], "Halo", "budget_closed")

    # ---- week 6: the reversal ------------------------------------------------
    add(at(5, 2, 16), "Slack", "#halo-build", DECISION_B, "Halo", "decision_dynamo")

    # ---- fenced sensitive material, scattered --------------------------------
    for n, (template, category) in enumerate(SENSITIVE):
        proj = list(PROJECTS)[n % 3]
        add(at(n % 9, (n * 2) % 5, 12, 10), "Slack", "Dev",
            template.format(p=proj), proj, f"sensitive_{category}_{n + 1}")
    # a second pass so every category appears more than once
    for n, (template, category) in enumerate(SENSITIVE):
        proj = list(PROJECTS)[(n + 1) % 3]
        add(at((n + 4) % 9, (n * 3) % 5, 15, 40), "WhatsApp", "Sana",
            template.format(p=proj), proj, f"sensitive_{category}_b{n + 1}")

    # ---- ephemeral statements -----------------------------------------------
    for n in range(20):
        proj = list(PROJECTS)[n % 3]
        add(at(n % 9, n % 5, 10, (n * 7) % 60), "Slack", "#halo-build",
            EPHEMERAL[n % len(EPHEMERAL)].format(p=proj), proj, f"ephemeral_{n + 1}")

    # ---- inference bait ------------------------------------------------------
    for n in range(6):
        proj = list(PROJECTS)[n % 3]
        add(at((n + 2) % 9, (n + 1) % 5, 17), "Notion", f"{proj} spec",
            INFERENCE_BAIT[n % len(INFERENCE_BAIT)].format(p=proj), proj,
            f"inference_{n + 1}")

    # ---- pure noise, to fill out the corpus ----------------------------------
    # Timestamps advance monotonically and content is drawn at random rather
    # than by modular arithmetic: `n % k` across several coprime periods made
    # every 120th record a byte-identical twin, which read as a bug in the
    # product and made the noise far less varied than the count suggested.
    need = target - len(records)
    WEEKS = 10

    for i in range(need):
        # Place each record at its proportional point in the 10-week window,
        # then nudge into working hours. Deriving the date from the index
        # rather than accumulating keeps the span exact however many records
        # are asked for.
        when = START + timedelta(days=(i / max(1, need)) * WEEKS * 7)
        # Weekends off. Saturday falls back to Friday and Sunday forward to
        # Monday, so the two do not both pile onto Friday.
        if when.weekday() == 5:
            when -= timedelta(days=1)
        elif when.weekday() == 6:
            when += timedelta(days=1)
        when = when.replace(hour=rng.randint(9, 18), minute=rng.randint(0, 59))

        proj = rng.choice(list(PROJECTS))
        app = rng.choice(list(APPS))
        # Destination has to track the project, or the corpus shows Halo work
        # being posted into the Ember channel and reads as noise about noise.
        dest = rng.choice(DEST_BY_PROJECT[app][proj])
        text = rng.choice(NOISE).format(p=proj)

        # A trailing specific makes two draws of the same template distinct,
        # the way two real dictations about the same chore would be.
        detail = rng.choice([
            "", "", "",
            " Nothing blocking.",
            " Will pick it up again tomorrow.",
            f" Left a note for {rng.choice(['Priya', 'Dev', 'Rohan', 'Sana'])}.",
            f" That closes out the {rng.choice(['first', 'second', 'third'])} batch.",
            " No changes needed after the review.",
            " Tagged it so it is easy to find later.",
        ])
        add(when, app, dest, (text + detail).strip(), proj, None)

    records.sort(key=lambda r: r["captured_at"])
    for i, rec in enumerate(records, start=1):
        rec["id"] = f"rec-{i:04d}"
    return records


def write_outputs(records: list[dict], out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    csv_path = out.with_suffix(".csv")
    cols = list(records[0].keys())
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(records)

    plants: dict[str, str] = {
        r["plant"]: r["id"] for r in records if r.get("plant")
    }
    manifest = {
        "seed": SEED,
        "records": len(records),
        "span": {"from": records[0]["captured_at"], "to": records[-1]["captured_at"]},
        "apps": sorted({r["app"] for r in records}),
        "projects": sorted({r["project"] for r in records if r["project"]}),
        "planted": plants,
        "counts": {
            "sensitive": sum(1 for r in records if (r.get("plant") or "").startswith("sensitive")),
            "ephemeral": sum(1 for r in records if (r.get("plant") or "").startswith("ephemeral")),
            "inference": sum(1 for r in records if (r.get("plant") or "").startswith("inference")),
            "style": sum(1 for r in records if (r.get("plant") or "").startswith("style")),
            "noise": sum(1 for r in records if not r.get("plant")),
        },
    }
    (out.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="corpus/kivi_corpus.jsonl")
    ap.add_argument("--count", type=int, default=520)
    args = ap.parse_args()

    records = build(args.count)
    manifest = write_outputs(records, Path(args.out))

    print(f"wrote {manifest['records']} records to {args.out}")
    print(f"  span    : {manifest['span']['from'][:10]} to {manifest['span']['to'][:10]}")
    print(f"  planted : {len(manifest['planted'])} labelled cases")
    for key, value in manifest["counts"].items():
        print(f"  {key:10s}: {value}")


if __name__ == "__main__":
    main()
