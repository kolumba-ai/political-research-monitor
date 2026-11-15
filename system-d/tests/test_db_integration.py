import math
import os
import re
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest

from system_d.agents.aggregator import aggregate_pending
from system_d.agents.question_designer import ensure_question_set
from system_d.agents.social_scorer import score_unscored
from system_d.config import Settings
from system_d.db import Store, get_pool
from system_d.llm import Usage
from system_d.migrate import apply_migration
from system_d.models import (
    CanonicalClaim,
    CommentScore,
    CommentScores,
    PollResult,
    QuestionScript,
    TranscriptMessage,
)
from system_d.seed import seed_data
from system_d.stopping import Stratum, compute_stats

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_D_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("SYSTEM_D_TEST_DATABASE_URL")


def test_schema_matches_design_sql_block_byte_for_byte():
    design = (REPO_ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    match = re.search(r"```sql\n(.*?)\n```", design, flags=re.DOTALL)
    assert match is not None
    expected = (match.group(1) + "\n").encode()
    assert (SYSTEM_D_DIR / "db" / "schema.sql").read_bytes() == expected


def _migration_table_exists(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('public.system_d_schema_migrations')").fetchone()[0] is not None


@pytest.fixture
def local_test_database_url():
    if not TEST_DATABASE_URL:
        pytest.skip("SYSTEM_D_TEST_DATABASE_URL is unset")
    parsed = urlsplit(TEST_DATABASE_URL)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("System D integration tests must use local PostgreSQL, never a hosted database")
    if parsed.path.rsplit("/", maxsplit=1)[-1] != "system_d_test":
        pytest.fail("System D integration tests must use the dedicated system_d_test database")
    return TEST_DATABASE_URL


def test_migration_applies_once_and_is_a_noop_on_second_run(local_test_database_url):
    with psycopg.connect(local_test_database_url) as conn:
        server_version = conn.execute("SHOW server_version_num").fetchone()[0]
        assert 140000 <= int(server_version) < 150000
        previous = (
            conn.execute("SELECT sha256 FROM system_d_schema_migrations WHERE filename = 'schema.sql'").fetchone()
            if _migration_table_exists(conn)
            else None
        )
        first_result = apply_migration(conn)
        second_result = apply_migration(conn)
        assert first_result is (previous is None)
        assert second_result is False
        assert conn.execute("SELECT sha256 FROM system_d_schema_migrations WHERE filename = 'schema.sql'").fetchone()
        assert conn.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()


def test_migration_rejects_a_changed_recorded_checksum(local_test_database_url, tmp_path):
    changed_schema = tmp_path / "schema.sql"
    changed_schema.write_text("-- changed schema\n", encoding="utf-8")
    with psycopg.connect(local_test_database_url) as conn:
        apply_migration(conn)
        with pytest.raises(SystemExit, match="migration checksum mismatch"):
            apply_migration(conn, changed_schema)


def test_seed_is_idempotent_and_strata_sum_to_one(local_test_database_url):
    with psycopg.connect(local_test_database_url) as conn:
        apply_migration(conn)
    settings = replace(Settings(), database_url=local_test_database_url)
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            store = Store(conn)
            seed_data(store)
            counts_after_first = {
                table: conn.execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"]
                for table in ("politicians", "claims", "social_reactions", "population_strata")
            }
            second_counts = seed_data(store)
            counts_after_second = {
                table: conn.execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"]
                for table in ("politicians", "claims", "social_reactions", "population_strata")
            }
            shares = conn.execute(
                """
                SELECT share FROM population_strata
                WHERE region IN ('Norte', 'Centro', 'Sur-Sureste')
                  AND age_band IN ('18-39', '40+')
                  AND gender IN ('F', 'M')
                """
            ).fetchall()
    finally:
        pool.close()
    assert second_counts == {
        "politicians": 0,
        "claims": 0,
        "reactions": 0,
        "strata": 0,
    }
    assert counts_after_first == counts_after_second
    assert len(shares) == 12
    assert math.fsum(float(row["share"]) for row in shares) == 1.0


class DatabaseFakeLLM:
    def complete_json(self, **kwargs):
        output_model = kwargs["output_model"]
        if output_model is CommentScores:
            indexes = [int(index) for index in re.findall(r"^\[(\d+)\]", kwargs["user"], flags=re.MULTILINE)]
            result = CommentScores(
                scores=[CommentScore(index=index, sentiment=0.25, stance="neutral") for index in indexes]
            )
        elif output_model is CanonicalClaim:
            result = CanonicalClaim(canonical_text="Canonical demo claim", testable=True)
        elif output_model is QuestionScript:
            result = QuestionScript(
                opening="Hola, esta encuesta es voluntaria.",
                belief_q="¿Cree que la afirmación es cierta?",
                sentiment_q="¿Cómo se siente sobre la persona?",
                open_q="¿Por qué respondió así?",
                closing="Gracias por su tiempo.",
            )
        else:
            raise AssertionError(f"unexpected output model: {output_model}")
        return result, Usage("claude-sonnet-5-5", 1, 1)


class DatabaseFakeEmbedder:
    def embed(self, texts):
        vector = [1.0] + [0.0] * 1535
        return [vector[:] for _ in texts], Usage("text-embedding-3-small", len(texts), 0)


def test_store_agent_round_trip_uses_local_postgres_and_pgvector(local_test_database_url):
    with psycopg.connect(local_test_database_url) as conn:
        apply_migration(conn)
    settings = replace(Settings(), database_url=local_test_database_url)
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            store = Store(conn)
            seed_data(store)
            llm = DatabaseFakeLLM()
            score_unscored(store, llm)
            aggregate_pending(store, llm, DatabaseFakeEmbedder(), settings)
            aggregates = store.get_testable_aggregated_claims()
            assert len(aggregates) >= 3
            aggregate_id = str(aggregates[0]["id"])
            assert store.get_social_sentiment(aggregate_id) is not None
            question_set = ensure_question_set(store, llm, aggregate_id)
            stratum = store.list_strata()[0]
            sim_run = f"db-test-{uuid.uuid4()}"
            respondent = store.add_respondent(
                str(stratum["id"]),
                {"name": "Integration respondent", "sim_run": sim_run},
            )
            reusable = store.available_simulated_respondents(str(stratum["id"]), sim_run, aggregate_id)
            assert any(item.id == respondent.id for item in reusable)
            result = PollResult(
                believes_claim=True,
                sentiment=0.5,
                completed=True,
                transcript=[
                    TranscriptMessage(role="agent", text="Pregunta"),
                    TranscriptMessage(role="respondent", text="Sí"),
                ],
            )
            store.insert_poll_response(
                aggregate_id,
                respondent.id,
                str(question_set["id"]),
                sim_run,
                1,
                result,
            )
            assert store.max_batch_no(aggregate_id, sim_run) == 1
            responses = store.responses_for(aggregate_id, sim_run, str(question_set["id"]))
            assert len(responses) == 1
            assert responses[0].believes_claim is True
            assert store.available_simulated_respondents(str(stratum["id"]), sim_run, aggregate_id) == []
            stats = compute_stats(
                responses,
                [Stratum(id=str(row["id"]), share=float(row["share"])) for row in store.list_strata()],
                0.25,
            )
            store.upsert_sampling_state(aggregate_id, sim_run, stats, "polling")
            assert store.get_sampling_state(aggregate_id, sim_run)["n_completed"] == 1
    finally:
        pool.close()
