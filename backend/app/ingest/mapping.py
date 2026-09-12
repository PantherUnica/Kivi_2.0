"""
Corpus field mapping.

The reviewer's corpus will not use our field names. Rather than make them edit
code, a mapping file declares which of their columns means what. Unmapped
fields are preserved verbatim in `interactions.meta`, so nothing is lost.

Default mapping matches corpus/kivi_corpus.jsonl. Override with:
    --mapping corpus/mapping.example.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as dateparser

DEFAULT_MAPPING: dict[str, Any] = {
    "external_id": ["id", "record_id", "external_id", "uuid", "dictation_id"],
    "captured_at": ["captured_at", "timestamp", "created_at", "time", "date", "ts"],
    "raw_asr": ["raw_asr", "asr", "raw", "raw_text", "asr_text", "transcript"],
    "formatted_text": ["formatted_text", "formatted", "llm_output", "text", "output",
                       "final_text", "clean_text"],
    "app": ["app", "application", "target_app", "source_app"],
    "destination": ["destination", "channel", "recipient", "audience", "to"],
    "project_hint": ["project", "project_hint", "workspace"],
    # A client is the audience; a project is the work. Folding one into the
    # other loses the name a person actually uses ("write it for Acme").
    "client": ["client", "account", "customer", "organisation", "organization"],
    "style_used": ["style", "style_used", "format_style"],
    "duration_ms": ["duration_ms", "duration", "length_ms"],
    "mode": ["mode", "kind", "type"],
}


@dataclass
class Mapping:
    fields: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_MAPPING))

    @classmethod
    def load(cls, path: str | None) -> "Mapping":
        if not path:
            return cls()
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
        merged = dict(DEFAULT_MAPPING)
        for key, names in user.items():
            merged[key] = ([names] if isinstance(names, str) else list(names)) + \
                merged.get(key, [])
        return cls(merged)

    def pick(self, record: dict, key: str) -> Any:
        for name in self.fields.get(key, []):
            if name in record and record[name] not in (None, ""):
                return record[name]
        return None

    def normalise(self, record: dict) -> dict:
        """Map one raw record into the shape the ingest pipeline expects."""
        raw = self.pick(record, "raw_asr")
        formatted = self.pick(record, "formatted_text")

        # A corpus with only one text field is still usable: the same text plays
        # both roles rather than the record being skipped.
        if raw and not formatted:
            formatted = raw
        if formatted and not raw:
            raw = formatted

        captured = self.pick(record, "captured_at")
        when = _parse_time(captured)

        mapped_names = {n for names in self.fields.values() for n in names}
        extra = {k: v for k, v in record.items() if k not in mapped_names}

        return {
            "external_id": str(self.pick(record, "external_id") or "") or None,
            "captured_at": when,
            "raw_asr": str(raw or ""),
            "formatted_text": str(formatted or ""),
            "app": _s(self.pick(record, "app")),
            "destination": _s(self.pick(record, "destination")),
            "project_hint": _s(self.pick(record, "project_hint")),
            "style_used": _s(self.pick(record, "style_used")),
            "duration_ms": _i(self.pick(record, "duration_ms")),
            "mode": _s(self.pick(record, "mode")) or "dictation",
            "client": _s(self.pick(record, "client")),
            "meta": extra,
        }


def _s(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


def _i(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value in (None, ""):
        dt = datetime.now(timezone.utc)
    else:
        try:
            dt = dateparser.parse(str(value))
        except (ValueError, OverflowError):
            dt = datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
