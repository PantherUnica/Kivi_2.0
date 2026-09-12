"""
ASR providers (DECISIONS.md D-10).

No speech recognition is implemented. The brief explicitly permits replaying
transcripts, and a mandatory microphone is the easiest way to break the
review path. The interface exists so a real recogniser drops in unchanged.
"""
from __future__ import annotations

from app.config import settings


class ReplayASR:
    """Reads the transcript that the corpus already carries."""

    name = "replay"

    def transcribe(self, audio_ref: str) -> str:
        return audio_ref


class IndicConformerASR:
    """
    Adapter stub for AI4Bharat IndicConformer-600M.

    Wire a local inference server or the transformers pipeline here; nothing
    else in the system changes.
    """

    name = "indicconformer"

    def transcribe(self, audio_ref: str) -> str:
        raise NotImplementedError(
            "IndicConformer adapter is declared but not wired. "
            "Use ASR_PROVIDER=replay (default) for the documented review path."
        )


class WhisperASR:
    """Adapter stub for whisper-large-v3-turbo."""

    name = "whisper"

    def transcribe(self, audio_ref: str) -> str:
        raise NotImplementedError(
            "Whisper adapter is declared but not wired. "
            "Use ASR_PROVIDER=replay (default) for the documented review path."
        )


def build_asr():
    return {
        "replay": ReplayASR,
        "indicconformer": IndicConformerASR,
        "whisper": WhisperASR,
    }.get(settings.asr_provider.lower(), ReplayASR)()
