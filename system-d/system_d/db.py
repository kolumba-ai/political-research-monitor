"""Database connection helpers and the shared SQL store."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import Settings
from .models import Respondent
from .stopping import Response


def _configure_connection(conn: Connection) -> None:
    register_vector(conn)


def _vector_list(vector: Any) -> list[float]:
    return vector.to_list() if hasattr(vector, "to_list") else list(vector)


def get_pool(settings: Settings) -> ConnectionPool:
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required")
    return ConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=max(1, min(settings.fleet_concurrency, 10)),
        kwargs={"row_factory": dict_row},
        configure=_configure_connection,
        open=True,
    )


class Store:
    def __init__(self, conn: Connection):
        self.conn = conn
        self.conn.row_factory = dict_row

    def get_politician(self, name: str) -> dict[str, Any] | None:
        return self.conn.execute("SELECT * FROM politicians WHERE name = %s", (name,)).fetchone()

    def get_politician_by_id(self, politician_id: str) -> dict[str, Any] | None:
        return self.conn.execute("SELECT * FROM politicians WHERE id = %s", (politician_id,)).fetchone()

    def add_politician(self, name: str, party: str | None, region: str | None, context: dict) -> str:
        existing = self.get_politician(name)
        if existing:
            return str(existing["id"])
        row = self.conn.execute(
            "INSERT INTO politicians (name, party, region, context) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, party, region, Jsonb(context)),
        ).fetchone()
        return str(row["id"])

    def find_claim(self, politician_id: str, claim_text: str) -> dict[str, Any] | None:
        return self.conn.execute(
            "SELECT * FROM claims WHERE politician_id = %s AND claim_text = %s ORDER BY created_at LIMIT 1",
            (politician_id, claim_text),
        ).fetchone()

    def add_claim(
        self, politician_id: str, claim_text: str, source_platform: str | None, source_url: str | None
    ) -> tuple[str, bool]:
        existing = self.find_claim(politician_id, claim_text)
        if existing:
            return str(existing["id"]), False
        row = self.conn.execute(
            """
            INSERT INTO claims (politician_id, claim_text, source_platform, source_url)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (politician_id, claim_text, source_platform, source_url),
        ).fetchone()
        return str(row["id"]), True

    def add_social_reaction(self, claim_id: str, platform: str | None, comment_text: str) -> bool:
        exists = self.conn.execute(
            "SELECT 1 FROM social_reactions WHERE claim_id = %s AND comment_text = %s LIMIT 1",
            (claim_id, comment_text),
        ).fetchone()
        if exists:
            return False
        self.conn.execute(
            """
            INSERT INTO social_reactions (claim_id, platform, comment_text, sentiment, stance)
            VALUES (%s, %s, %s, NULL, NULL)
            """,
            (claim_id, platform, comment_text),
        )
        return True

    def add_stratum(
        self, region: str, age_band: str, gender: str, urban_rural: str | None, share: float
    ) -> tuple[str, bool]:
        existing = self.conn.execute(
            """
            SELECT id FROM population_strata
            WHERE region = %s AND age_band = %s AND gender = %s
            """,
            (region, age_band, gender),
        ).fetchone()
        if existing:
            return str(existing["id"]), False
        row = self.conn.execute(
            """
            INSERT INTO population_strata (region, age_band, gender, urban_rural, share)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (region, age_band, gender, urban_rural, share),
        ).fetchone()
        return str(row["id"]), True

    def list_strata(self) -> list[dict[str, Any]]:
        return self.conn.execute(
            """
            SELECT id, region, age_band, gender, urban_rural, share
            FROM population_strata
            ORDER BY region, age_band, gender
            """
        ).fetchall()

    def list_claims_without_embedding(self) -> list[dict[str, Any]]:
        return self.conn.execute(
            "SELECT id, politician_id, claim_text FROM claims WHERE embedding IS NULL ORDER BY created_at"
        ).fetchall()

    def update_claim_embedding(self, claim_id: str, embedding: list[float]) -> None:
        self.conn.execute(
            "UPDATE claims SET embedding = %s WHERE id = %s",
            (Vector(embedding), claim_id),
        )

    def pending_claims_for_aggregation(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT c.id, c.politician_id, c.claim_text, c.embedding, p.name AS politician_name
            FROM claims c
            JOIN politicians p ON p.id = c.politician_id
            WHERE c.aggregated_claim_id IS NULL AND c.embedding IS NOT NULL
            ORDER BY c.created_at, c.id
            """
        ).fetchall()
        for row in rows:
            row["embedding"] = _vector_list(row["embedding"])
        return rows

    def nearest_aggregates(
        self, politician_id: str, embedding: list[float], limit: int = 100
    ) -> list[tuple[str, list[float]]]:
        rows = self.conn.execute(
            """
            SELECT id, embedding FROM aggregated_claims
            WHERE politician_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (politician_id, Vector(embedding), limit),
        ).fetchall()
        return [(str(row["id"]), _vector_list(row["embedding"])) for row in rows]

    def add_aggregated_claim(
        self, politician_id: str, canonical_text: str, testable: bool, embedding: list[float]
    ) -> str:
        row = self.conn.execute(
            """
            INSERT INTO aggregated_claims (politician_id, canonical_text, testable, embedding, claim_count)
            VALUES (%s, %s, %s, %s, 1) RETURNING id
            """,
            (politician_id, canonical_text, testable, Vector(embedding)),
        ).fetchone()
        return str(row["id"])

    def link_claim_to_aggregate(self, claim_id: str, aggregated_claim_id: str) -> None:
        self.conn.execute(
            "UPDATE claims SET aggregated_claim_id = %s WHERE id = %s",
            (aggregated_claim_id, claim_id),
        )

    def increment_aggregate_count(self, aggregated_claim_id: str) -> None:
        self.conn.execute(
            "UPDATE aggregated_claims SET claim_count = claim_count + 1 WHERE id = %s",
            (aggregated_claim_id,),
        )

    def get_testable_aggregated_claims(
        self, claim_ids: list[str] | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT ac.id, ac.politician_id, ac.canonical_text, ac.claim_count, ac.testable,
                   p.name AS politician_name, p.party, p.region, p.context
            FROM aggregated_claims ac
            JOIN politicians p ON p.id = ac.politician_id
            WHERE ac.testable
        """
        params: list[Any] = []
        if claim_ids is not None:
            if not claim_ids:
                return []
            query += " AND ac.id = ANY(%s::uuid[])"
            params.append(claim_ids)
        query += " ORDER BY ac.created_at, ac.id"
        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def get_claim_info(self, aggregated_claim_id: str) -> dict[str, Any] | None:
        return self.conn.execute(
            """
            SELECT ac.id, ac.politician_id, ac.canonical_text, ac.testable,
                   p.name AS politician_name, p.party, p.region, p.context
            FROM aggregated_claims ac
            JOIN politicians p ON p.id = ac.politician_id
            WHERE ac.id = %s
            """,
            (aggregated_claim_id,),
        ).fetchone()

    def get_social_sentiment(self, aggregated_claim_id: str) -> float | None:
        row = self.conn.execute(
            """
            SELECT AVG(sr.sentiment) AS sentiment
            FROM social_reactions sr
            JOIN claims c ON c.id = sr.claim_id
            WHERE c.aggregated_claim_id = %s AND sr.sentiment IS NOT NULL
            """,
            (aggregated_claim_id,),
        ).fetchone()
        return None if row["sentiment"] is None else float(row["sentiment"])

    def unscored_reactions(self) -> list[dict[str, Any]]:
        return self.conn.execute(
            """
            SELECT sr.id AS reaction_id, sr.claim_id, sr.comment_text,
                   c.claim_text, p.name AS politician_name
            FROM social_reactions sr
            JOIN claims c ON c.id = sr.claim_id
            JOIN politicians p ON p.id = c.politician_id
            WHERE sr.sentiment IS NULL
            ORDER BY sr.created_at, sr.id
            """
        ).fetchall()

    def update_social_reaction(self, reaction_id: str, sentiment: float, stance: str) -> None:
        self.conn.execute(
            "UPDATE social_reactions SET sentiment = %s, stance = %s WHERE id = %s",
            (sentiment, stance, reaction_id),
        )

    def get_unscored_claim_comments(self) -> list[dict[str, Any]]:
        return self.conn.execute(
            """
            SELECT c.id AS claim_id, c.claim_text, p.name AS politician_name,
                   array_agg(sr.id ORDER BY sr.created_at, sr.id) AS reaction_ids,
                   array_agg(sr.comment_text ORDER BY sr.created_at, sr.id) AS comments
            FROM claims c
            JOIN politicians p ON p.id = c.politician_id
            JOIN social_reactions sr ON sr.claim_id = c.id
            WHERE sr.sentiment IS NULL
            GROUP BY c.id, c.claim_text, p.name
            ORDER BY c.created_at, c.id
            """
        ).fetchall()

    def latest_question_set(self, aggregated_claim_id: str) -> dict[str, Any] | None:
        return self.conn.execute(
            """
            SELECT id, version, script FROM question_sets
            WHERE aggregated_claim_id = %s
            ORDER BY version DESC LIMIT 1
            """,
            (aggregated_claim_id,),
        ).fetchone()

    def get_question_set_by_id(self, question_set_id: str) -> dict[str, Any] | None:
        return self.conn.execute(
            "SELECT id, version, script FROM question_sets WHERE id = %s",
            (question_set_id,),
        ).fetchone()

    def add_question_set(self, aggregated_claim_id: str, version: int, script: dict) -> dict[str, Any]:
        row = self.conn.execute(
            """
            INSERT INTO question_sets (aggregated_claim_id, version, script)
            VALUES (%s, %s, %s) RETURNING id, version, script
            """,
            (aggregated_claim_id, version, Jsonb(script)),
        ).fetchone()
        return row

    def available_simulated_respondents(
        self, stratum_id: str, sim_run: str, aggregated_claim_id: str
    ) -> list[Respondent]:
        rows = self.conn.execute(
            """
            SELECT r.id, r.stratum_id, r.persona, r.channel
            FROM respondents r
            WHERE r.stratum_id = %s AND r.is_simulated
              AND r.channel = 'simulator' AND r.persona->>'sim_run' = %s
              AND NOT EXISTS (
                  SELECT 1 FROM poll_responses pr
                  WHERE pr.respondent_id = r.id
                    AND pr.aggregated_claim_id = %s AND pr.sim_run = %s
              )
            ORDER BY r.created_at, r.id
            """,
            (stratum_id, sim_run, aggregated_claim_id, sim_run),
        ).fetchall()
        return [
            Respondent(
                id=str(row["id"]),
                stratum_id=str(row["stratum_id"]),
                persona=row["persona"],
                channel=row["channel"],
            )
            for row in rows
        ]

    def add_respondent(self, stratum_id: str, persona: dict, channel: str = "simulator") -> Respondent:
        row = self.conn.execute(
            """
            INSERT INTO respondents (stratum_id, is_simulated, persona, channel)
            VALUES (%s, TRUE, %s, %s) RETURNING id, stratum_id, persona, channel
            """,
            (stratum_id, Jsonb(persona), channel),
        ).fetchone()
        return Respondent(
            id=str(row["id"]),
            stratum_id=str(row["stratum_id"]),
            persona=row["persona"],
            channel=row["channel"],
        )

    def get_respondent(self, respondent_id: str) -> Respondent | None:
        row = self.conn.execute(
            "SELECT id, stratum_id, persona, channel FROM respondents WHERE id = %s",
            (respondent_id,),
        ).fetchone()
        if row is None:
            return None
        return Respondent(
            id=str(row["id"]),
            stratum_id=str(row["stratum_id"]),
            persona=row["persona"],
            channel=row["channel"],
        )

    def responses_for(self, aggregated_claim_id: str, sim_run: str, question_set_id: str) -> list[Response]:
        rows = self.conn.execute(
            """
            SELECT r.stratum_id, pr.completed, pr.believes_claim, pr.sentiment
            FROM poll_responses pr
            JOIN respondents r ON r.id = pr.respondent_id
            WHERE pr.aggregated_claim_id = %s AND pr.sim_run = %s
              AND pr.question_set_id = %s
            ORDER BY pr.created_at, pr.id
            """,
            (aggregated_claim_id, sim_run, question_set_id),
        ).fetchall()
        return [
            Response(
                stratum_id=str(row["stratum_id"]),
                completed=row["completed"],
                believes_claim=row["believes_claim"],
                sentiment=row["sentiment"],
            )
            for row in rows
        ]

    def get_sampling_state(self, aggregated_claim_id: str, sim_run: str) -> dict[str, Any] | None:
        return self.conn.execute(
            """
            SELECT * FROM claim_sampling_state
            WHERE aggregated_claim_id = %s AND sim_run = %s
            """,
            (aggregated_claim_id, sim_run),
        ).fetchone()

    def upsert_sampling_state(
        self,
        aggregated_claim_id: str,
        sim_run: str,
        stats: Any,
        status: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO claim_sampling_state (
                aggregated_claim_id, sim_run, n_completed, p_believe, ci_low, ci_high,
                sentiment_mean, sentiment_ci_low, sentiment_ci_high, social_sentiment, status, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (aggregated_claim_id, sim_run) DO UPDATE SET
                n_completed = EXCLUDED.n_completed,
                p_believe = EXCLUDED.p_believe,
                ci_low = EXCLUDED.ci_low,
                ci_high = EXCLUDED.ci_high,
                sentiment_mean = EXCLUDED.sentiment_mean,
                sentiment_ci_low = EXCLUDED.sentiment_ci_low,
                sentiment_ci_high = EXCLUDED.sentiment_ci_high,
                social_sentiment = EXCLUDED.social_sentiment,
                status = EXCLUDED.status,
                updated_at = now()
            """,
            (
                aggregated_claim_id,
                sim_run,
                stats.n_completed,
                stats.p_believe,
                stats.ci_low,
                stats.ci_high,
                stats.sentiment_mean,
                stats.sentiment_ci_low,
                stats.sentiment_ci_high,
                stats.social_sentiment,
                status,
            ),
        )

    def max_batch_no(self, aggregated_claim_id: str, sim_run: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(batch_no), 0) AS batch_no
            FROM poll_responses
            WHERE aggregated_claim_id = %s AND sim_run = %s
            """,
            (aggregated_claim_id, sim_run),
        ).fetchone()
        return int(row["batch_no"])

    def insert_poll_response(
        self,
        aggregated_claim_id: str,
        respondent_id: str,
        question_set_id: str,
        sim_run: str,
        batch_no: int,
        result: Any,
    ) -> str:
        transcript = [item.model_dump() if hasattr(item, "model_dump") else item for item in result.transcript]
        row = self.conn.execute(
            """
            INSERT INTO poll_responses (
                aggregated_claim_id, respondent_id, question_set_id, sim_run,
                batch_no, channel, transcript, sentiment, believes_claim, completed
            ) VALUES (%s, %s, %s, %s, %s, 'simulator', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                aggregated_claim_id,
                respondent_id,
                question_set_id,
                sim_run,
                batch_no,
                Jsonb(transcript),
                result.sentiment,
                result.believes_claim,
                result.completed,
            ),
        ).fetchone()
        return str(row["id"])

    def list_claim_reactions(self, claim_id: str) -> list[dict[str, Any]]:
        return self.conn.execute(
            "SELECT id, comment_text, platform FROM social_reactions WHERE claim_id = %s ORDER BY created_at, id",
            (claim_id,),
        ).fetchall()

    def claims_for_post(self, politician_id: str, claim_texts: Iterable[str]) -> list[dict[str, Any]]:
        texts = list(claim_texts)
        if not texts:
            return []
        return self.conn.execute(
            """
            SELECT * FROM claims
            WHERE politician_id = %s AND claim_text = ANY(%s)
            ORDER BY created_at, id
            """,
            (politician_id, texts),
        ).fetchall()
