"""Read-only Neon queries and formatting helpers for the System D dashboard."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

STATUS_LABELS = {
    "polling": "polling",
    "converged": "converged",
    "decisive": "decisive",
    "capped": "inconclusive (capped)",
}

RUNS_SQL = """
SELECT sim_run, count(*) AS claims, count(*) FILTER (WHERE status <> 'polling') AS terminal,
       max(updated_at) AS updated_at
FROM claim_sampling_state
GROUP BY sim_run
ORDER BY max(updated_at) DESC
"""

CLAIMS_SQL = """
SELECT s.aggregated_claim_id::text AS claim_id, p.name AS politician, p.party, a.canonical_text, a.claim_count,
       s.status, s.n_completed, s.p_believe, s.ci_low, s.ci_high, s.sentiment_mean, s.sentiment_ci_low,
       s.sentiment_ci_high, s.social_sentiment, s.updated_at
FROM claim_sampling_state s
JOIN aggregated_claims a ON a.id = s.aggregated_claim_id
JOIN politicians p ON p.id = a.politician_id
WHERE s.sim_run = %s
ORDER BY p.name, a.canonical_text
"""

STRATA_SQL = """
SELECT ps.region, ps.age_band, ps.gender, ps.urban_rural, ps.share,
       count(pr.id) AS attempted,
       count(pr.id) FILTER (WHERE pr.completed) AS completed,
       avg(CASE WHEN pr.believes_claim THEN 1.0 ELSE 0.0 END) FILTER (WHERE pr.completed) AS share_believe,
       avg(pr.sentiment) FILTER (WHERE pr.completed) AS sentiment_mean
FROM population_strata ps
LEFT JOIN respondents r ON r.stratum_id = ps.id
LEFT JOIN poll_responses pr ON pr.respondent_id = r.id AND pr.aggregated_claim_id = %s AND pr.sim_run = %s
GROUP BY ps.id, ps.region, ps.age_band, ps.gender, ps.urban_rural, ps.share
ORDER BY ps.share DESC, ps.region, ps.age_band
"""

TRANSCRIPTS_SQL = """
SELECT pr.id::text AS response_id, pr.batch_no, pr.completed, pr.believes_claim, pr.sentiment, pr.transcript,
       pr.created_at, r.is_simulated, r.persona, ps.region, ps.age_band, ps.gender, ps.urban_rural
FROM poll_responses pr
JOIN respondents r ON r.id = pr.respondent_id
LEFT JOIN population_strata ps ON ps.id = r.stratum_id
WHERE pr.aggregated_claim_id = %s AND pr.sim_run = %s
ORDER BY pr.created_at
LIMIT %s
"""

SOCIAL_SQL = """
SELECT sr.platform, sr.comment_text, sr.sentiment, sr.stance
FROM social_reactions sr
JOIN claims c ON c.id = sr.claim_id
WHERE c.aggregated_claim_id = %s
ORDER BY sr.created_at
"""


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    path = os.environ.get("DATABASE_URL_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text().strip()
    raise RuntimeError("Set DATABASE_URL (or DATABASE_URL_FILE) to the System D Neon database.")


@contextmanager
def read_only_connection(url: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
    conn.read_only = True
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def fetch(url: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with read_only_connection(url) as conn:
        return conn.execute(sql, params).fetchall()


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "")


def fmt_share_ci(p: float | None, low: float | None, high: float | None) -> str:
    if p is None:
        return "—"
    if low is None or high is None:
        return f"{p:.0%}"
    return f"{p:.0%} ({low:.0%}–{high:.0%})"


def fmt_signed_ci(mean: float | None, low: float | None, high: float | None) -> str:
    if mean is None:
        return "—"
    if low is None or high is None:
        return f"{mean:+.2f}"
    return f"{mean:+.2f} [{low:+.2f}, {high:+.2f}]"


def fmt_signed(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}"


def gap(polled: float | None, social: float | None) -> float | None:
    if polled is None or social is None:
        return None
    return polled - social


def belief_label(value: bool | None, completed: bool) -> str:
    if not completed:
        return "incomplete"
    if value is None:
        return "no sé / no answer"
    return "sí" if value else "no"


def persona_summary(persona: dict[str, Any] | None) -> str:
    if not persona:
        return ""
    parts = [
        str(persona.get("name", "")),
        f"{persona['age']} años" if persona.get("age") is not None else "",
        str(persona.get("occupation", "")),
        str(persona.get("state", "")),
    ]
    summary = ", ".join(p for p in parts if p)
    leaning = persona.get("political_leaning")
    return f"{summary}. Postura: {leaning}" if leaning else summary


def stratum_label(row: dict[str, Any]) -> str:
    return ", ".join(str(row[k]) for k in ("region", "age_band", "gender", "urban_rural") if row.get(k))
