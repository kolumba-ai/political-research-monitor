"""EvidenceGraph's Postgres connection and isolated migration tracking."""
from __future__ import annotations

from pathlib import Path

from socialclaims.db import connect

MIGRATIONS = Path(__file__).parent / "migrations"


def migrate(conn) -> list[str]:
    applied: list[str] = []
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS ev_schema_migrations "
                     "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {row["name"] for row in conn.execute("SELECT name FROM ev_schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO ev_schema_migrations(name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied
