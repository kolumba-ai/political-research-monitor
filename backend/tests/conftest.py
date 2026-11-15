import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polgraph.config import Settings  # noqa: E402
from polgraph.exa_gateway import ExaGateway  # noqa: E402
from polgraph.graph.store import SqliteGraphStore  # noqa: E402
from polgraph.state import PipelineState  # noqa: E402

from .fakes import FakeExa  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, exa_api_key="test", data_dir=tmp_path, graph_backend="sqlite",
                    exa_requests_per_second=1000, exa_run_budget_usd=20, exa_total_budget_usd=50)


@pytest.fixture
def fake_exa():
    return FakeExa()


@pytest.fixture
def gateway(settings, fake_exa):
    return ExaGateway(settings, client=fake_exa, sleep=lambda s: None)


_BACKENDS = ["sqlite"] + (["neo4j"] if os.environ.get("POLGRAPH_TEST_NEO4J_WIPE") == "1" else [])


@pytest.fixture(params=_BACKENDS)
def store(request, tmp_path):
    """SQLite always; Neo4j too when POLGRAPH_TEST_NEO4J_WIPE=1 (WARNING: deletes every node in that database)."""
    if request.param == "sqlite":
        yield SqliteGraphStore(tmp_path / "g.sqlite3")
        return
    from polgraph.config import Settings as S
    from polgraph.graph.store import Neo4jGraphStore
    cfg = S()
    st = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    with st.driver.session() as sess:
        sess.run("MATCH (n) DETACH DELETE n")
    yield st
    with st.driver.session() as sess:
        sess.run("MATCH (n) DETACH DELETE n")
    st.close()


@pytest.fixture
def state(tmp_path):
    return PipelineState(tmp_path / "p.sqlite3")
