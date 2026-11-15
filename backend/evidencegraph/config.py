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
    mapper_version: str = "m1"
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_requests_per_second: float = 2.0
    pastdev_requests_per_second: float = 2.0
    enrich_provider: str = "anthropic"  # or "openai"
    enrich_model: str = "claude-sonnet-5-5"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    openai_requests_per_second: float = 5.0
    enrich_results_per_query: int = 5
    enrich_max_documents: int = 8
    enrich_page_chars: int = 24_000
    # How long to wait for past.dev to finish a batch; unfinished propositions stay queued and a later run only
    # re-projects them (their judgements are reused, no new search).
    enrich_poll_timeout_s: float = 20.0
    # false: judge into Neon only; propositions wait as `pastdev_pending` for `sync-pastdev` to write them.
    enrich_pastdev: bool = True
    # sync-pastdev keeps past.dev's unprocessed queue below this many minutes of its indexing throughput, so
    # anything sent is searchable within about that time; the rest waits in Neon as `pastdev_pending`.
    pastdev_max_queue_minutes: float = 30.0
    pastdev_bytes_per_hour: int = 600_000  # observed: ~400 records of ~1.5 KB an hour
    # Query-plan rows to search (comma-separated subset of neutral,counter,primary,indicator,fact_check,expert,english).
    enrich_rows: str = "neutral,counter,primary,indicator,fact_check,expert,english"
    # Prices used for the spend cap and estimates; check them against the real rates of the model and plan.
    enrich_usd_per_mtok_in: float = 3.0
    enrich_usd_per_mtok_out: float = 15.0
    pastdev_usd_per_credit: float = 0.0003


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
        exa_run_budget_usd=settings.ev_exa_monthly_budget_usd,
        data_dir=REPO_ROOT / "var",
    )
    limiter = PgRateLimiter(settings.database_url, settings.exa_api_key or "", 8.0)
    return ExaGateway(polgraph_settings, client=client, db_path=db_path, limiter=limiter,
                      budget_scope="ev", monthly_budget_usd=settings.ev_exa_monthly_budget_usd)
