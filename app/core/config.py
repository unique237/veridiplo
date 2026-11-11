"""Central runtime configuration (env-driven, no secrets in code)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Infrastructure
    DATABASE_URL: str = "postgresql+psycopg://veridiplo:veridiplo@db:5432/veridiplo"
    KG_URL: str = "http://kg:7878"
    KB_DIR: str = "/app/kb"
    DATA_DIR: str = "/app/data"
    API_URL: str = "http://api:8000"  # used by the UI

    # Behaviour
    POLICY_MODE: Literal["on", "off"] = "on"  # on = Version B (governed), off = Version A
    LLM_PROVIDER: Literal["none", "ollama", "anthropic", "openai"] = "none"
    SEED: int = 42
    UI_DEFAULT_LANG: Literal["fr", "en"] = "fr"

    # Embeddings
    EMBED_MODEL: str = "intfloat/multilingual-e5-small"

    # LLM backends (all optional; offline demo needs none)
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b-instruct"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    @property
    def cloud_llm(self) -> bool:
        """True when personal data could leave the machine (cross-border transfer warning)."""
        return self.LLM_PROVIDER in ("anthropic", "openai")


@lru_cache
def get_settings() -> Settings:
    return Settings()
