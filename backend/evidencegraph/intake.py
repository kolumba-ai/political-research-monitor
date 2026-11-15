"""Transactional System B proposition intake."""
from __future__ import annotations

from pathlib import Path

SQL_DIR = Path(__file__).parent / "sql"


class _DryRunRollback(Exception):
    def __init__(self, result: dict):
        self.result = result


def _counts(conn, politicians: list[str] | None) -> dict:
    rows = conn.execute(
        "SELECT status, skip_reason, count(*) AS count FROM ev_proposition_queue "
        "WHERE (%(politicians)s::text[] IS NULL OR politician_ids && %(politicians)s::text[]) "
        "GROUP BY status, skip_reason",
        {"politicians": politicians},
    ).fetchall()
    status: dict[str, int] = {}
    skip_reason: dict[str, int] = {}
    for row in rows:
        status[row["status"]] = status.get(row["status"], 0) + row["count"]
        if row["skip_reason"]:
            skip_reason[row["skip_reason"]] = skip_reason.get(row["skip_reason"], 0) + row["count"]
    return {"status": status, "skip_reason": skip_reason}


def run_intake(conn, politicians: list[str] | None = None, min_cw: float = 0.5,
               mapper_version: str = "m0", dry_run: bool = False) -> dict:
    params = {
        "politicians": politicians or None,
        "min_cw": min_cw,
        "mapper_version": mapper_version,
    }
    try:
        with conn.transaction():
            intake_sql = (SQL_DIR / "intake.sql").read_text()
            stale_sql = (SQL_DIR / "intake_stale.sql").read_text()
            rows = conn.execute(intake_sql, params).fetchall()
            params["seen"] = [row["proposition_id"] for row in rows]
            stale = conn.execute(stale_sql, params)
            result = _counts(conn, params["politicians"])
            result.update({"upserted": len(rows), "stale": stale.rowcount})
            if dry_run:
                raise _DryRunRollback(result)
            return result
    except _DryRunRollback as rollback:
        return rollback.result


def queue_rows(conn, status: str = "queued", limit: int = 20) -> list[dict]:
    return conn.execute(
        "SELECT * FROM ev_proposition_queue WHERE status = %s ORDER BY priority DESC LIMIT %s",
        (status, limit),
    ).fetchall()
