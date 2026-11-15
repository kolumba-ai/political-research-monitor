"""Runtime configuration, loaded from environment variables / `.env`."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Exa ---------------------------------------------------------------
    exa_api_key: str | None = None
    exa_requests_per_second: float = 4.0
    exa_limiter_database_url: str | None = None
    exa_shared_requests_per_second: float = 8.0
    exa_max_retries: int = 5
    # Hard ceiling for a single pipeline invocation (USD). Calls that would exceed it are refused.
    exa_run_budget_usd: float = 15.0
    # Lifetime ceiling across all runs recorded in the usage ledger (USD).
    exa_total_budget_usd: float = 45.0
    # Exa Agent: `auto` effort honours budget.maxCostDollars as a hard cap per run.
    exa_agent_effort: str = "auto"
    exa_agent_max_cost_usd: float = 2.0
    exa_agent_timeout_s: int = 1800
    cache_ttl_hours: int = 24 * 7

    # --- Storage -------------------------------------------------------------
    graph_backend: Literal["sqlite", "neo4j"] = "sqlite"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None
    data_dir: Path = REPO_ROOT / "var"
    # System B social-claims export read by /api/claims (written by `python -m socialclaims pull-pastdev`).
    claims_export_dir: Path = REPO_ROOT / "var" / "socialclaims" / "claims"

    # --- Incremental refresh: stage freshness windows (days) -----------------
    ttl_democracy_days: int = 180
    ttl_contact_days: int = 180
    ttl_structure_days: int = 90
    ttl_officeholders_days: int = 7

    # --- Eligibility policy (see polgraph/eligibility.py) --------------------
    democracy_accepted_freedom_house: str = "free"
    democracy_accepted_vdem: str = "liberal_democracy,electoral_democracy"
    democracy_accepted_eiu: str = "full_democracy,flawed_democracy"
    democracy_min_sources: int = 2
    democracy_max_age_years: int = 3
    contact_accepted_statuses: str = "allowed"
    contact_relevant_purposes: str = "issue_communication,fact_verification_request"
    contact_relevant_senders: str = "organization"
    require_human_review: bool = True

    # --- Verification ----------------------------------------------------------
    min_independent_domains: int = 2
    corroboration_search: bool = True

    # --- Server ---------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    log_level: str = "INFO"
    log_json: bool = Field(default=False)

    @staticmethod
    def csv(value: str) -> set[str]:
        return {v.strip().lower() for v in value.split(",") if v.strip()}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
