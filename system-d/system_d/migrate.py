"""Apply the System D schema once, guarded by a PostgreSQL advisory lock."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from .config import Settings

MIGRATION_FILENAME = "schema.sql"
ADVISORY_LOCK_ID = 0x535953444D494752
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / MIGRATION_FILENAME


def apply_migration(conn: psycopg.Connection, schema_path: Path = SCHEMA_PATH) -> bool:
    sql_bytes = schema_path.read_bytes()
    checksum = hashlib.sha256(sql_bytes).hexdigest()
    sql = sql_bytes.decode("utf-8")
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_d_schema_migrations (
                filename text primary key,
                sha256 text not null,
                applied_at timestamptz default now()
            )
            """
        )
        conn.commit()
        existing = conn.execute(
            "SELECT sha256 FROM system_d_schema_migrations WHERE filename = %s",
            (MIGRATION_FILENAME,),
        ).fetchone()
        if existing:
            recorded_checksum = existing["sha256"] if isinstance(existing, dict) else existing[0]
            if recorded_checksum != checksum:
                raise SystemExit(
                    f"migration checksum mismatch for {MIGRATION_FILENAME}: "
                    f"recorded {recorded_checksum}, current {checksum}"
                )
            return False
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "INSERT INTO system_d_schema_migrations (filename, sha256) VALUES (%s, %s)",
                (MIGRATION_FILENAME, checksum),
            )
        return True
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
        conn.commit()


def main() -> None:
    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(settings.database_url) as conn:
        applied = apply_migration(conn)
    print("applied schema.sql" if applied else "already applied")


if __name__ == "__main__":
    main()
