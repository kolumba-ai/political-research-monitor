"""Runtime configuration for System D."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

DEFAULT_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "gpt-6.1-sol": {"input": 2.00, "output": 10.00},
    "gpt-6-luna": {"input": 0.10, "output": 0.50},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
}


def provider_for(model: str) -> Literal["openai", "anthropic"]:
    return "openai" if model.startswith(("gpt-", "o1", "o3", "o4")) else "anthropic"


def _read_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    return default if value is None or value == "" else int(value)


def _read_float(env: Mapping[str, str], name: str, default: float) -> float:
    value = env.get(name)
    return default if value is None or value == "" else float(value)


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    llm_model: str = "gpt-6.1-sol"
    simulator_model: str | None = "gpt-6-luna"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    seed_batch: int = 30
    next_batch: int = 20
    target_half_width: float = 0.07
    max_calls: int = 400
    min_per_stratum: int = 5
    fleet_concurrency: int = 50
    llm_max_retries: int = 6
    llm_backoff_base_s: float = 1.0
    llm_backoff_max_s: float = 60.0
    aggregate_similarity: float = 0.90
    max_usd: float = 5.0
    personas_per_call: int = 8
    prices_usd_per_million: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: {model: prices.copy() for model, prices in DEFAULT_PRICES_USD_PER_MILLION.items()}
    )

    def __post_init__(self) -> None:
        if self.simulator_model is None:
            object.__setattr__(self, "simulator_model", self.llm_model)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        llm_model = env.get("LLM_MODEL") or "gpt-6.1-sol"
        return cls(
            database_url=env.get("DATABASE_URL") or None,
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            openai_api_key=env.get("OPENAI_API_KEY") or None,
            openai_base_url=env.get("OPENAI_BASE_URL") or "https://api.openai.com",
            llm_model=llm_model,
            simulator_model=env.get("SIMULATOR_MODEL") or "gpt-6-luna",
            embedding_model=env.get("EMBEDDING_MODEL") or "text-embedding-3-small",
            embedding_dim=_read_int(env, "EMBEDDING_DIM", 1536),
            seed_batch=_read_int(env, "SEED_BATCH", 30),
            next_batch=_read_int(env, "NEXT_BATCH", 20),
            target_half_width=_read_float(env, "TARGET_HALF_WIDTH", 0.07),
            max_calls=_read_int(env, "MAX_CALLS", 400),
            min_per_stratum=_read_int(env, "MIN_PER_STRATUM", 5),
            fleet_concurrency=_read_int(env, "FLEET_CONCURRENCY", 50),
            llm_max_retries=_read_int(env, "LLM_MAX_RETRIES", 6),
            llm_backoff_base_s=_read_float(env, "LLM_BACKOFF_BASE_S", 1.0),
            llm_backoff_max_s=_read_float(env, "LLM_BACKOFF_MAX_S", 60.0),
            aggregate_similarity=_read_float(env, "AGGREGATE_SIMILARITY", 0.90),
            max_usd=_read_float(env, "MAX_USD", 5.0),
            personas_per_call=_read_int(env, "PERSONAS_PER_CALL", 8),
        )

    def stopping_config(self):
        from .stopping import Config

        return Config(
            seed_batch=self.seed_batch,
            next_batch=self.next_batch,
            target_half_width=self.target_half_width,
            max_calls=self.max_calls,
            min_per_stratum=self.min_per_stratum,
        )
