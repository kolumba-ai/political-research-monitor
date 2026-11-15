from urllib.parse import urlsplit

import psycopg
import pytest

from evidencegraph.db import migrate as migrate_evidencegraph
from evidencegraph.config import EvidenceSettings
from socialclaims.db import connect, migrate as migrate_socialclaims

TEST_DATABASE_URL = "postgresql://ubuntu:ubuntu@localhost:5432/socialclaims_test"


@pytest.fixture
def conn():
    url = TEST_DATABASE_URL
    if urlsplit(url).path.rstrip("/").split("/")[-1] != "socialclaims_test":
        raise RuntimeError("EvidenceGraph tests must use the socialclaims_test database")
    try:
        connection = connect(url)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres test database unavailable: {exc}")
    with connection.transaction():
        connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate_socialclaims(connection)
    migrate_evidencegraph(connection)
    yield connection
    connection.close()


@pytest.fixture
def ev_settings(tmp_path):
    return EvidenceSettings(_env_file=None, database_url=TEST_DATABASE_URL, snapshot_dir=tmp_path / "snapshots",
                            fetch_min_interval_s=0.0)
