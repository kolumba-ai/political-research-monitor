"""Export System D's simulated polling of linked storylines as `opinion.json` for the claims explorer.

The explorer reads it next to `storylines.json` (`<CLAIMS_EXPORT_DIR>/<ISO2>/opinion.json`). Only aggregates linked
to a storyline (`aggregated_claims.storyline_id`, see `link_storylines`) are exported; every respondent is simulated.

    DATABASE_URL=<System D database> .venv/bin/python -m system_d.export_opinion --out ../var/socialclaims/claims/MX/opinion.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

STATES_SQL = """
SELECT a.storyline_id, a.storyline_label, a.canonical_text, p.person_id, s.sim_run, s.status, s.n_completed,
       s.p_believe, s.ci_low, s.ci_high, s.sentiment_mean, s.sentiment_ci_low, s.sentiment_ci_high,
       s.social_sentiment, s.updated_at, s.aggregated_claim_id,
       (SELECT count(*) FROM social_reactions sr JOIN claims c ON c.id = sr.claim_id
        WHERE c.aggregated_claim_id = a.id AND sr.sentiment IS NOT NULL) AS social_comments
FROM claim_sampling_state s
JOIN aggregated_claims a ON a.id = s.aggregated_claim_id
JOIN politicians p ON p.id = a.politician_id
WHERE a.storyline_id IS NOT NULL
ORDER BY a.storyline_id, s.updated_at
"""

STRATA_SQL = """
SELECT pr.aggregated_claim_id, pr.sim_run, ps.region, ps.age_band, ps.gender, count(*) AS n,
       avg(CASE WHEN pr.believes_claim THEN 1.0 ELSE 0.0 END) AS share_believe, avg(pr.sentiment) AS sentiment_mean
FROM poll_responses pr
JOIN respondents r ON r.id = pr.respondent_id
JOIN population_strata ps ON ps.id = r.stratum_id
JOIN aggregated_claims a ON a.id = pr.aggregated_claim_id
WHERE pr.completed AND a.storyline_id IS NOT NULL
GROUP BY 1, 2, 3, 4, 5
ORDER BY 3, 4, 5
"""

_FIELDS = (
    "status",
    "n_completed",
    "p_believe",
    "ci_low",
    "ci_high",
    "sentiment_mean",
    "sentiment_ci_low",
    "sentiment_ci_high",
    "social_sentiment",
    "social_comments",
)


def _num(value: Any) -> Any:
    return round(float(value), 4) if value is not None and not isinstance(value, (int, str)) else value


def build(states: list[dict[str, Any]], strata: list[dict[str, Any]]) -> dict[str, Any]:
    """One entry per storyline: every run's result, and `latest` the most recently updated run that finished
    (or the most recent run when none has finished)."""
    by_run: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in strata:
        by_run.setdefault((str(s["aggregated_claim_id"]), s["sim_run"]), []).append(
            {
                "region": s["region"],
                "age_band": s["age_band"],
                "gender": s["gender"],
                "n": s["n"],
                "share_believe": _num(s["share_believe"]),
                "sentiment_mean": _num(s["sentiment_mean"]),
            }
        )
    out: dict[str, Any] = {}
    for s in states:
        entry = out.setdefault(
            s["storyline_id"],
            {
                "storyline_id": s["storyline_id"],
                "label": s["storyline_label"],
                "politician_id": s["person_id"],
                "polled_claim": s["canonical_text"],
                "latest": None,
                "runs": {},
            },
        )
        run = {k: _num(s[k]) for k in _FIELDS}
        run["gap"] = (
            None
            if s["sentiment_mean"] is None or s["social_sentiment"] is None
            else _num(s["sentiment_mean"] - s["social_sentiment"])
        )
        run["updated_at"] = s["updated_at"].isoformat() if s["updated_at"] else None
        run["strata"] = by_run.get((str(s["aggregated_claim_id"]), s["sim_run"]), [])
        entry["runs"][s["sim_run"]] = run
    for entry in out.values():
        runs = sorted(entry["runs"].items(), key=lambda kv: kv[1]["updated_at"] or "")
        finished = [name for name, r in runs if r["status"] != "polling"]
        entry["latest"] = (finished or [name for name, _ in runs])[-1]
    return out


def export(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.read_only = True
        storylines = build(conn.execute(STATES_SQL).fetchall(), conn.execute(STRATA_SQL).fetchall())
    return {
        "source": "system_d",
        "simulated": True,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "storylines": storylines,
    }


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(f.name, path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="opinion.json path, next to storylines.json")
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    args = parser.parse_args(argv)
    payload = export(os.environ[args.database_url_env])
    write_atomic(args.out, payload)
    print(f"wrote {len(payload['storylines'])} storylines to {args.out}")


if __name__ == "__main__":
    main()
