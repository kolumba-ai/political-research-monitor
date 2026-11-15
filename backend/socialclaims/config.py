"""System B runtime configuration, loaded from environment variables / `.env` (shared with System A)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Oriane bills per row returned, by projection.
ORIANE_CREDITS_PER_ROW = {"basic": 2, "default": 3, "full": 4}


class SocialSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    # --- System A (read-only) -------------------------------------------------
    # `knowledge_base`: read the exported JSONL snapshot; `graph`: read System A's live store (GRAPH_BACKEND).
    system_a_source: Literal["knowledge_base", "bundles", "graph"] = "knowledge_base"
    system_a_kb_dir: Path = REPO_ROOT / "knowledge_base"
    system_a_bundle_dir: Path = REPO_ROOT / "knowledge_base" / "bundles"

    # --- Postgres (System B source of truth) ------------------------------------
    database_url: str = "postgresql://ubuntu:ubuntu@localhost:5432/socialclaims"
    rate_limiter_database_url: str | None = None
    anthropic_requests_per_second: float = 2.0
    oriane_requests_per_second: float = 1.0
    pastdev_requests_per_second: float = 2.0

    # --- Oriane ----------------------------------------------------------------
    oriane_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    claim_model: str = "claude-sonnet-5-5"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    openai_requests_per_second: float = 5.0
    # Skip posts already extracted ok under any extractor version (fill gaps with a different model).
    claim_skip_any_version: bool = False
    claim_max_posts_per_run: int = 200
    oriane_base_url: str = "https://connect.oriane.xyz"
    oriane_timeout_s: float = 60.0
    oriane_max_retries: int = 4
    oriane_projection: Literal["basic", "default", "full"] = "full"
    oriane_page_size: int = 100
    # Hard monthly credit ceiling across all runs; calls whose worst case would exceed it are refused.
    oriane_monthly_credit_ceiling: int = 28_000
    # Per-run credit ceiling (worst case checked before every page).
    oriane_run_credit_ceiling: int = 4_000
    oriane_max_filter_values: int = 500
    max_posts_per_politician: int = 1_000
    default_window_days: int = 90
    platforms: str = "tiktok,instagram"

    # --- past.dev claim memory (one project per country; key in PAST_DEV_API_KEY) ---------
    past_dev_api_key: str | None = None
    past_dev_base_url: str = "https://api.past.dev/api/v1"
    past_dev_timeout_s: float = 30.0
    # Local export read by the API and the explorer (`pull-pastdev`); git-ignored under var/.
    claims_export_dir: Path = REPO_ROOT / "var" / "socialclaims" / "claims"

    # --- Raw storage / retention (configurable until provider agreements settle) --
    raw_storage_dir: Path = REPO_ROOT / "var" / "socialclaims" / "raw"
    store_raw_payloads: bool = True
    thumbnail_dir: Path = REPO_ROOT / "var" / "socialclaims" / "thumbnails"
    # Days to keep raw payloads; 0 = keep until a retention policy is set.
    raw_retention_days: int = 0
    # Fields dropped before anything is stored (data minimisation); recorded per raw row.
    excluded_fields: str = "profileEmails"

    # --- Relevance ---------------------------------------------------------------
    relevance_keep_threshold: float = 0.5
    relevance_review_threshold: float = 0.3

    @staticmethod
    def csv(value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]


@lru_cache
def get_settings() -> SocialSettings:
    return SocialSettings()
