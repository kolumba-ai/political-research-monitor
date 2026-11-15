"""EvidenceGraph configuration from the shared repository environment."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class EvidenceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://ubuntu:ubuntu@localhost:5432/socialclaims"
    exa_api_key: str | None = None
    anthropic_api_key: str | None = None
    past_dev_api_key: str | None = None
    past_dev_base_url: str = "https://api.past.dev/api/v1"
    ev_exa_monthly_budget_usd: float = 30.0
    fetch_user_agent: str = "EvidenceGraphBot/0.1"
    fetch_min_interval_s: float = 1.0
    fetch_timeout_s: float = 20.0
    fetch_max_bytes: int = 15_000_000
    snapshot_dir: Path = REPO_ROOT / "var" / "evidencegraph" / "snapshots"
    intake_min_checkworthiness: float = 0.5
    mapper_version: str = "m0"


@lru_cache
def get_settings() -> EvidenceSettings:
    return EvidenceSettings()


def exa_gateway(settings: EvidenceSettings, client=None, db_path=None):
    from polgraph.config import Settings
    from polgraph.exa_gateway import ExaGateway, PgRateLimiter

    polgraph_settings = Settings(
        _env_file=None,
        exa_api_key=settings.exa_api_key,
        exa_requests_per_second=8.0,
        data_dir=REPO_ROOT / "var",
    )
    limiter = PgRateLimiter(settings.database_url, settings.exa_api_key or "", 8.0)
    return ExaGateway(polgraph_settings, client=client, db_path=db_path, limiter=limiter,
                      budget_scope="ev", monthly_budget_usd=settings.ev_exa_monthly_budget_usd)
