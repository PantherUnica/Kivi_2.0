"""
Provider interfaces (DECISIONS.md D-07, D-09, D-10).

Everything that talks to a model goes through here, so that:
  * swapping Sarvam <-> local Qwen3-8B <-> mock is one env var;
  * every call is metered (tokens, latency, cost) in one place.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import settings


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the model's reply as JSON, tolerating prose and code fences."""
        return extract_json(self.text)


def extract_json(text: str) -> Any:
    """
    Pull the first well-formed JSON value out of a model reply.

    Models wrap JSON in prose or fences more often than they should; failing
    the whole ingest for that would be silly. Returns None if nothing parses,
    and the caller decides what that means.
    """
    if not text:
        return None
    text = text.strip()

    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def estimate_tokens(text: str) -> int:
    """Cheap token estimate, used only when a provider returns no usage block."""
    return max(1, len(text) // 4)


def price(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens / 1_000_000 * settings.price_input_per_mtok
        + completion_tokens / 1_000_000 * settings.price_output_per_mtok
    )


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult: ...


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ASRProvider(Protocol):
    name: str

    def transcribe(self, audio_ref: str) -> str: ...


# ------------------------------------------------------------------ factories
_llm: LLMProvider | None = None
_embedder: EmbeddingProvider | None = None
_asr: ASRProvider | None = None


def get_llm() -> LLMProvider:
    global _llm
    if _llm is None:
        kind = settings.llm_provider.lower()
        if kind == "sarvam":
            from app.providers.llm_http import SarvamLLM

            _llm = SarvamLLM()
        elif kind in ("openai_compatible", "openai", "local"):
            from app.providers.llm_http import OpenAICompatibleLLM

            _llm = OpenAICompatibleLLM()
        else:
            from app.providers.llm_mock import MockLLM

            _llm = MockLLM()
    return _llm


def get_embedder() -> EmbeddingProvider:
    global _embedder
    if _embedder is None:
        from app.providers.embeddings import build_embedder

        _embedder = build_embedder()
    return _embedder


def get_asr() -> ASRProvider:
    global _asr
    if _asr is None:
        from app.providers.asr import build_asr

        _asr = build_asr()
    return _asr
