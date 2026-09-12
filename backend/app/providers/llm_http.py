"""
HTTP LLM providers.

Both speak the OpenAI chat-completions shape, which is what Sarvam, vLLM,
Ollama and LM Studio all expose - so one client covers hosted open-weight
(sarvam-m) and local open-weight (Qwen3-8B) alike (D-07).
"""
from __future__ import annotations

import logging
import time

import httpx

from app.config import settings
from app.providers.base import LLMResult, estimate_tokens, price

log = logging.getLogger(__name__)


class _ChatCompletionsLLM:
    name = "http"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=settings.llm_timeout_s)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        started = time.perf_counter()
        resp = self._client.post(
            f"{self.base_url}/chat/completions", json=payload, headers=headers
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            # Surface the failure. Never silently fall back to a weaker model -
            # that would make an evaluation run mean something different than
            # it claims to mean.
            raise RuntimeError(
                f"{self.name} returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or estimate_tokens(system + user))
        ct = int(usage.get("completion_tokens") or estimate_tokens(text))

        return LLMResult(
            text=text,
            model=self.model,
            prompt_tokens=pt,
            completion_tokens=ct,
            latency_ms=latency_ms,
            cost_usd=price(pt, ct),
            raw=data,
        )


class SarvamLLM(_ChatCompletionsLLM):
    name = "sarvam"

    def __init__(self) -> None:
        if not settings.sarvam_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=sarvam but SARVAM_API_KEY is empty. "
                "Set it in .env, or use LLM_PROVIDER=mock for a zero-key run."
            )
        super().__init__(settings.sarvam_base_url, settings.sarvam_api_key, settings.sarvam_model)


class OpenAICompatibleLLM(_ChatCompletionsLLM):
    name = "openai_compatible"

    def __init__(self) -> None:
        if not settings.llm_base_url:
            raise RuntimeError(
                "LLM_PROVIDER=openai_compatible but LLM_BASE_URL is empty. "
                "Point it at your vLLM / Ollama / LM Studio server, e.g. "
                "http://host.docker.internal:11434/v1"
            )
        super().__init__(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
