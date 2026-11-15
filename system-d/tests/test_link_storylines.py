import json
import os
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg.rows import dict_row

from system_d.link_storylines import link_plan, main, plan_storylines, remove_links
from system_d.migrate import apply_migrations, migration_paths

SHEINBAUM = "person:MX:claudia-sheinbaum-pardo"
BRUGADA = "person:MX:clara-marina-brugada-molina"


def _claim(claim_id, pid, prop, published, text=None, **extra):
    return {
        "claim_id": claim_id,
        "politician_id": pid,
        "politician_name": pid.rsplit(":", 1)[-1],
        "proposition_id": prop,
        "text": text or f"statement {claim_id}",
        "canonical_text": f"canonical {prop}",
        "platform": "tiktok",
        "url": f"https://example.org/{claim_id}",
        "oriane_id": f"post-{claim_id}",
        "published_at": published,
        **extra,
    }


CLAIMS = [
    _claim("c1", SHEINBAUM, "p1", "2026-09-02"),
    _claim("c2", SHEINBAUM, "p1", "2026-09-01"),
    _claim("c3", SHEINBAUM, "p2", "2026-09-03"),
    _claim("c4", SHEINBAUM, "p3", "2026-09-04"),
    _claim("c5", SHEINBAUM, None, "2026-09-05"),
    _claim("c6", BRUGADA, "p9", "2026-09-06"),
]
STORYLINES = {
    "format": "socialclaims-storylines/1",
    "politicians": {
        SHEINBAUM: {
            "storylines": {
                "sl-a": {
                    "storyline_id": "sl-a",
                    "label": "Security",
                    "topic": "seguridad",
                    "versions": {
                        "sv-a": {"version_id": "sv-a", "kind": "claim", "units": ["prop:p1"]},
                        "sv-a2": {"version_id": "sv-a2", "kind": "claim", "units": ["prop:p2"]},
                        "sv-ax": {"version_id": "sv-ax", "kind": "counter_claim", "units": ["prop:p3"]},
                    },
                },
                "sl-b": {
                    "storyline_id": "sl-b",
                    "label": "Single",
                    "versions": {
                        "sv-b": {"version_id": "sv-b", "kind": "claim", "units": ["claim:c5"]},
                    },
                },
            }
        },
        BRUGADA: {
            "storylines": {
                "sl-c": {
                    "storyline_id": "sl-c",
                    "label": "Utopias",
                    "versions": {
                        "sv-c": {"version_id": "sv-c", "kind": "claim", "units": ["prop:p9"]},
                    },
                },
            }
        },
    },
}


def test_plan_takes_claim_versions_skips_counter_claims_and_small_storylines():
    plans = plan_storylines(CLAIMS, STORYLINES, [SHEINBAUM])
    assert [p.storyline_id for p in plans] == ["sl-a"]
    plan = plans[0]
    assert [c["claim_id"] for c in plan.claims] == ["c2", "c1", "c3"]
    assert plan.canonical_text == "canonical p1"
    assert (plan.person_id, plan.label, plan.topic) == (SHEINBAUM, "Security", "seguridad")


def test_plan_orders_by_statements_and_applies_top_per_politician():
    plans = plan_storylines(CLAIMS, STORYLINES, top=1, min_statements=1)
    assert [(p.person_id, p.storyline_id) for p in plans] == [(BRUGADA, "sl-c"), (SHEINBAUM, "sl-a")]
    assert [p.storyline_id for p in plan_storylines(CLAIMS, STORYLINES, [SHEINBAUM], min_statements=1)] == [
        "sl-a",
        "sl-b",
    ]


def test_plan_ignores_unknown_politicians_and_claims_without_text():
    claims = [*CLAIMS, {"claim_id": "c7", "politician_id": SHEINBAUM, "proposition_id": "p1"}]
    assert plan_storylines(claims, STORYLINES, ["person:MX:nobody"]) == []
    assert len(plan_storylines(claims, STORYLINES, [SHEINBAUM])[0].claims) == 3


def test_migration_paths_apply_schema_first_then_numbered_files():
    names = [path.name for path in migration_paths()]
    assert names[0] == "schema.sql"
    assert "002_storylines.sql" in names
    assert names[1:] == sorted(names[1:])


def test_dry_run_prints_plan_without_a_database(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "claims.jsonl").write_text("\n".join(json.dumps(c) for c in CLAIMS) + "\n", encoding="utf-8")
    (tmp_path / "storylines.json").write_text(json.dumps(STORYLINES), encoding="utf-8")
    main(["--export-dir", str(tmp_path), "--politicians", SHEINBAUM, "--dry-run"])
    assert capsys.readouterr().out == f"{SHEINBAUM}\tsl-a\t3\tcanonical p1\n"


@pytest.fixture
def conn():
    url = os.environ.get("SYSTEM_D_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SYSTEM_D_TEST_DATABASE_URL is unset")
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.path.rsplit("/", 1)[-1] != "system_d_test":
        pytest.fail("System D integration tests must use the local system_d_test database")
    with psycopg.connect(url, row_factory=dict_row) as connection:
        apply_migrations(connection)
        remove_links(connection)
        yield connection
        remove_links(connection)


def _count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()["count"]


def test_link_is_idempotent_and_remove_deletes_only_linked_rows(conn):
    demo_aggregates = _count(conn, "SELECT count(*) FROM aggregated_claims WHERE storyline_id IS NULL")
    plan = plan_storylines(CLAIMS, STORYLINES, [SHEINBAUM])[0]
    comments = {"post-c2": ["muy bien", "mentira"], "post-c1": ["no le creo"]}
    first = link_plan(conn, plan, comments, max_comments=2)
    second = link_plan(conn, plan, comments, max_comments=2)
    assert first["created"] is True and second["created"] is False
    assert first["aggregated_claim_id"] == second["aggregated_claim_id"]
    assert (first["reactions_added"], second["reactions_added"]) == (2, 0)
    row = conn.execute(
        """
        SELECT ac.storyline_id, ac.storyline_label, ac.claim_count, ac.canonical_text, p.person_id
        FROM aggregated_claims ac JOIN politicians p ON p.id = ac.politician_id WHERE ac.id = %s
        """,
        (first["aggregated_claim_id"],),
    ).fetchone()
    assert row == {
        "storyline_id": "sl-a",
        "storyline_label": "Security",
        "claim_count": 3,
        "canonical_text": "canonical p1",
        "person_id": SHEINBAUM,
    }
    assert _count(conn, "SELECT count(*) FROM claims WHERE source_claim_id = ANY(%s)", ["c1", "c2", "c3"]) == 3

    counts = remove_links(conn)
    assert counts["aggregated_claims"] == 1 and counts["claims"] == 3 and counts["social_reactions"] == 2
    assert counts["politicians"] == 1
    assert _count(conn, "SELECT count(*) FROM aggregated_claims WHERE storyline_id IS NOT NULL") == 0
    assert _count(conn, "SELECT count(*) FROM aggregated_claims WHERE storyline_id IS NULL") == demo_aggregates
