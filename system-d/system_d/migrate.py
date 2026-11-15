"""Apply the System D schema and its numbered migrations once each, guarded by a PostgreSQL advisory lock."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from .config import Settings

MIGRATION_FILENAME = "schema.sql"
ADVISORY_LOCK_ID = 0x535953444D494752
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / MIGRATION_FILENAME
MIGRATIONS_DIR = SCHEMA_PATH.parent / "migrations"


def migration_paths(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    return [SCHEMA_PATH, *sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))]


def apply_migration(
    conn: psycopg.Connection, schema_path: Path = SCHEMA_PATH, filename: str = MIGRATION_FILENAME
) -> bool:
    sql_bytes = schema_path.read_bytes()
    checksum = hashlib.sha256(sql_bytes).hexdigest()
    sql = sql_bytes.decode("utf-8")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_ID,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_d_schema_migrations (
                filename text primary key,
                sha256 text not null,
                applied_at timestamptz default now()
            )
            """
        )
        existing = conn.execute(
            "SELECT sha256 FROM system_d_schema_migrations WHERE filename = %s",
            (filename,),
        ).fetchone()
        if existing:
            recorded_checksum = existing["sha256"] if isinstance(existing, dict) else existing[0]
            if recorded_checksum != checksum:
                raise SystemExit(
                    f"migration checksum mismatch for {filename}: recorded {recorded_checksum}, current {checksum}"
                )
            return False
        conn.execute(sql)
        conn.execute(
            "INSERT INTO system_d_schema_migrations (filename, sha256) VALUES (%s, %s)",
            (filename, checksum),
        )
        return True


def apply_migrations(conn: psycopg.Connection, paths: list[Path] | None = None) -> list[str]:
    """Apply `schema.sql`, then each `db/migrations/NNN_*.sql` in order; returns the files applied now."""
    applied = []
    for path in migration_paths() if paths is None else paths:
        if apply_migration(conn, path, path.name):
            applied.append(path.name)
    return applied


def main() -> None:
    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(settings.database_url) as conn:
        applied = apply_migrations(conn)
    print(f"applied {', '.join(applied)}" if applied else "already applied")


if __name__ == "__main__":
    main()
