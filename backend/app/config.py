"""Central configuration. Every value is env-driven and documented in .env.example."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- database ---
    database_url: str = "postgresql+psycopg://kivi:kivi@db:5432/kivi"

    # --- llm (D-07) ---
    llm_provider: str = "mock"           # mock | sarvam | openai_compatible
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai/v1"
    sarvam_model: str = "sarvam-m"
    llm_base_url: str = ""
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3:8b"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1200
    llm_timeout_s: int = 90
    llm_max_concurrency: int = 6

    # --- embeddings (D-09) ---
    embedding_provider: str = "fastembed"   # fastembed | qwen3 | hashing
    embed_dim: int = 384
    embed_model: str = "BAAI/bge-small-en-v1.5"

    # --- asr (D-10) ---
    asr_provider: str = "replay"

    # --- memory policy (D-13, D-14) ---
    policy_duplicate_cosine: float = 0.88
    policy_contradiction_cosine: float = 0.55
    policy_promote_after_corroborations: int = 2

    # --- rag (D-16, D-17) ---
    rag_top_k_memories: int = 8
    rag_top_k_chunks: int = 10
    rag_top_k_episodes: int = 6
    rag_rrf_k: int = 60
    rag_support_threshold: float = 0.28
    rag_min_similarity: float = 0.42
    rag_w_similarity: float = 0.50
    rag_w_confidence: float = 0.25
    rag_w_recency: float = 0.15
    rag_w_scope: float = 0.20
    rag_p_inactive: float = 0.30
    rag_p_superseded_source: float = 0.35

    # --- cost meter ---
    price_input_per_mtok: float = 0.10
    price_output_per_mtok: float = 0.30

    # --- app ---
    kivi_user_handle: str = "maya"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
