"""
Embedding providers (DECISIONS.md D-09).

Default: fastembed / BAAI/bge-small-en-v1.5 (384-d, CPU, ~130MB).
Upgrade:  Qwen3-Embedding-0.6B (1024-d) - set EMBEDDING_PROVIDER=qwen3, EMBED_DIM=1024.
Fallback: a deterministic hashed-ngram vector that needs no network at all.

The fallback is used automatically if a model cannot be loaded, and it says so
loudly - a silent downgrade would make the evaluation numbers a lie.
"""
from __future__ import annotations

import hashlib
import logging
import re

import numpy as np

from app.config import settings

log = logging.getLogger(__name__)


class HashingEmbedder:
    """
    Deterministic character/word n-gram hashing into a fixed-dim unit vector.

    Not a neural embedding. It captures lexical overlap only, which is enough
    to keep the pipeline honest offline, and it is flagged everywhere it is used.
    """

    name = "hashing"
    degraded = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        grams = [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
        return words + grams

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for tok in self._tokens(text):
                h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
                vec[h % self.dim] += 1.0 if h % 2 else -1.0
            norm = np.linalg.norm(vec)
            out.append((vec / norm if norm else vec).tolist())
        return out


class FastEmbedEmbedder:
    name = "fastembed"
    degraded = False

    def __init__(self, model_name: str, dim: int) -> None:
        from fastembed import TextEmbedding

        self.dim = dim
        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = list(self._model.embed(texts))
        out = []
        for v in vecs:
            v = np.asarray(v, dtype=np.float32)
            norm = np.linalg.norm(v)
            out.append((v / norm if norm else v).tolist())
        return out


def build_embedder():
    kind = settings.embedding_provider.lower()
    dim = settings.embed_dim

    if kind == "hashing":
        log.warning("EMBEDDING_PROVIDER=hashing - lexical-only vectors, not neural.")
        return HashingEmbedder(dim)

    model_name = settings.embed_model
    if kind == "qwen3":
        model_name = "Qwen/Qwen3-Embedding-0.6B"

    try:
        emb = FastEmbedEmbedder(model_name, dim)
        probe = emb.embed(["dimension probe"])[0]
        if len(probe) != dim:
            raise RuntimeError(
                f"EMBED_DIM={dim} but {model_name} produces {len(probe)} dims. "
                f"Set EMBED_DIM={len(probe)} in .env and run `make reset`."
            )
        log.info("Embeddings: %s (%d dims)", model_name, dim)
        return emb
    except Exception as exc:  # noqa: BLE001 - degrade loudly, never silently
        log.error(
            "Could not load embedding model %s (%s). "
            "FALLING BACK TO HASHING EMBEDDINGS - retrieval quality will be lexical only. "
            "This is reported in the evaluation output.",
            model_name,
            exc,
        )
        return HashingEmbedder(dim)
