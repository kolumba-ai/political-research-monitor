"""Load the explorer's storylines into System D as aggregated claims, so every poll is tied to a real storyline.

Reads `storylines.json` and `claims.jsonl` from a socialclaims export (`var/socialclaims/claims/<ISO2>`). Each
selected storyline becomes one `aggregated_claims` row keyed by `storyline_id`; its statements become `claims` rows
keyed by `source_claim_id`. Counter-claim versions are skipped: the poll tests the storyline's claim itself.
Optionally copies real post comments from System B (`segment.source = 'comment'`) into `social_reactions`.
Re-running is idempotent; `--remove` deletes everything this command created, including its poll results.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings


@dataclass
class StorylinePlan:
    person_id: str
    politician_name: str
    storyline_id: str
    label: str
    topic: str | None
    canonical_text: str
    claims: list[dict[str, Any]] = field(default_factory=list)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    # split on "\n" only: captions may hold U+2028 and similar, which str.splitlines() treats as line ends
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def unit_key(claim: dict[str, Any]) -> str:
    return f"prop:{claim['proposition_id']}" if claim.get("proposition_id") else f"claim:{claim['claim_id']}"


def _source(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": claim["claim_id"],
        "text": claim.get("text") or claim.get("canonical_text"),
        "platform": claim.get("platform"),
        "url": claim.get("url") or claim.get("post_url"),
        "oriane_id": claim.get("oriane_content_id") or claim.get("oriane_id"),
        "published_at": claim.get("published_at") or claim.get("stated_at") or "",
    }


def plan_storylines(
    claims: Iterable[dict[str, Any]],
    storylines: dict[str, Any],
    person_ids: list[str] | None = None,
    top: int = 10,
    min_statements: int = 2,
) -> list[StorylinePlan]:
    """The `top` storylines per politician with the most statements, each with its claim statements, oldest first."""
    by_unit: dict[tuple[str, str], list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    for claim in claims:
        pid = claim.get("politician_id")
        if not pid or not claim.get("claim_id") or not (claim.get("text") or claim.get("canonical_text")):
            continue
        by_unit.setdefault((pid, unit_key(claim)), []).append(claim)
        names.setdefault(pid, claim.get("politician_name") or pid)
    politicians = storylines.get("politicians") or {}
    plans: list[StorylinePlan] = []
    for pid in person_ids or sorted(politicians):
        candidates = []
        for sid, story in ((politicians.get(pid) or {}).get("storylines") or {}).items():
            versions = [v for v in (story.get("versions") or {}).values() if v.get("kind") != "counter_claim"]
            rows_by_version = {
                v["version_id"]: [c for unit in v.get("units") or [] for c in by_unit.get((pid, unit), [])]
                for v in versions
            }
            statements = sorted(
                {c["claim_id"]: _source(c) for rows in rows_by_version.values() for c in rows}.values(),
                key=lambda c: (c["published_at"], c["claim_id"]),
            )
            if len(statements) < max(min_statements, 1):
                continue
            main = max(sorted(rows_by_version), key=lambda vid: len(rows_by_version[vid]))
            first = min(rows_by_version[main], key=lambda c: (_source(c)["published_at"], c["claim_id"]))
            canonical = first.get("canonical_text") or first.get("text")
            candidates.append(
                StorylinePlan(
                    person_id=pid,
                    politician_name=names.get(pid, pid),
                    storyline_id=sid,
                    label=story.get("label") or "",
                    topic=story.get("topic"),
                    canonical_text=canonical,
                    claims=statements,
                )
            )
        candidates.sort(key=lambda p: (-len(p.claims), p.storyline_id))
        plans.extend(candidates[:top])
    return plans


def fetch_comments(source_database_url: str, oriane_ids: list[str], per_post: int = 20) -> dict[str, list[str]]:
    """Distinct comment texts per post from System B's `segment` table, read-only."""
    if not oriane_ids:
        return {}
    with psycopg.connect(source_database_url, row_factory=dict_row) as conn:
        conn.read_only = True
        rows = conn.execute(
            """
            SELECT oriane_id, text FROM (
              SELECT oriane_id, text, row_number() OVER (PARTITION BY oriane_id ORDER BY min(ordinal), text) AS rank
              FROM segment
              WHERE source = 'comment' AND oriane_id = ANY(%s) AND length(btrim(text)) > 0
              GROUP BY oriane_id, text
            ) ranked
            WHERE rank <= %s
            ORDER BY oriane_id, rank
            """,
            (oriane_ids, per_post),
        ).fetchall()
    comments: dict[str, list[str]] = {}
    for row in rows:
        comments.setdefault(row["oriane_id"], []).append(row["text"])
    return comments


def _politician(conn: psycopg.Connection, plan: StorylinePlan) -> str:
    row = conn.execute(
        """
        INSERT INTO politicians (name, context, person_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (person_id) DO UPDATE SET person_id = EXCLUDED.person_id
        RETURNING id
        """,
        (plan.politician_name, Jsonb({"source": "socialclaims storylines"}), plan.person_id),
    ).fetchone()
    return str(row["id"])


def link_plan(
    conn: psycopg.Connection,
    plan: StorylinePlan,
    comments: dict[str, list[str]] | None = None,
    max_comments: int = 40,
) -> dict[str, Any]:
    politician_id = _politician(conn, plan)
    aggregate = conn.execute(
        """
        INSERT INTO aggregated_claims (politician_id, canonical_text, claim_count, testable, storyline_id, storyline_label)
        VALUES (%s, %s, %s, true, %s, %s)
        ON CONFLICT (storyline_id) DO UPDATE SET storyline_label = EXCLUDED.storyline_label
        RETURNING id, (xmax = 0) AS created
        """,
        (politician_id, plan.canonical_text, len(plan.claims), plan.storyline_id, plan.label),
    ).fetchone()
    aggregate_id = str(aggregate["id"])
    first_claim_of_post: dict[str, str] = {}
    for claim in plan.claims:
        row = conn.execute(
            """
            INSERT INTO claims (politician_id, claim_text, source_platform, source_url, aggregated_claim_id,
                                source_claim_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_claim_id) DO UPDATE SET aggregated_claim_id = EXCLUDED.aggregated_claim_id
            RETURNING id
            """,
            (politician_id, claim["text"], claim["platform"], claim["url"], aggregate_id, claim["claim_id"]),
        ).fetchone()
        if claim["oriane_id"]:
            first_claim_of_post.setdefault(claim["oriane_id"], str(row["id"]))
    conn.execute(
        """
        UPDATE aggregated_claims
        SET claim_count = (SELECT count(*) FROM claims WHERE aggregated_claim_id = %s)
        WHERE id = %s
        """,
        (aggregate_id, aggregate_id),
    )
    reactions = 0
    stored = conn.execute(
        "SELECT count(*) AS count FROM social_reactions sr JOIN claims c ON c.id = sr.claim_id "
        "WHERE c.aggregated_claim_id = %s",
        (aggregate_id,),
    ).fetchone()["count"]
    for oriane_id, claim_id in first_claim_of_post.items():
        platform = next(c["platform"] for c in plan.claims if c["oriane_id"] == oriane_id)
        for text in (comments or {}).get(oriane_id, []):
            if stored + reactions >= max_comments:
                break
            inserted = conn.execute(
                """
                INSERT INTO social_reactions (claim_id, platform, comment_text)
                SELECT %s, %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM social_reactions WHERE claim_id = %s AND comment_text = %s)
                """,
                (claim_id, platform, text, claim_id, text),
            ).rowcount
            reactions += inserted
    return {
        "aggregated_claim_id": aggregate_id,
        "created": bool(aggregate["created"]),
        "claims": len(plan.claims),
        "reactions_added": reactions,
    }


def remove_links(conn: psycopg.Connection, person_ids: list[str] | None = None) -> dict[str, int]:
    """Delete storyline-linked aggregates with their claims, comments, question sets and poll results."""
    aggregates = [
        row["id"]
        for row in conn.execute(
            """
            SELECT ac.id FROM aggregated_claims ac JOIN politicians p ON p.id = ac.politician_id
            WHERE ac.storyline_id IS NOT NULL AND (%s::text[] IS NULL OR p.person_id = ANY(%s::text[]))
            """,
            (person_ids, person_ids),
        ).fetchall()
    ]
    counts: dict[str, int] = {}
    statements = [
        ("poll_responses", "DELETE FROM poll_responses WHERE aggregated_claim_id = ANY(%s)"),
        ("claim_sampling_state", "DELETE FROM claim_sampling_state WHERE aggregated_claim_id = ANY(%s)"),
        ("question_sets", "DELETE FROM question_sets WHERE aggregated_claim_id = ANY(%s)"),
        (
            "social_reactions",
            "DELETE FROM social_reactions WHERE claim_id IN (SELECT id FROM claims WHERE aggregated_claim_id = ANY(%s))",
        ),
        ("claims", "DELETE FROM claims WHERE aggregated_claim_id = ANY(%s)"),
        ("aggregated_claims", "DELETE FROM aggregated_claims WHERE id = ANY(%s)"),
    ]
    for table, sql in statements:
        counts[table] = conn.execute(sql, (aggregates,)).rowcount
    counts["politicians"] = conn.execute(
        """
        DELETE FROM politicians p
        WHERE p.person_id IS NOT NULL AND (%s::text[] IS NULL OR p.person_id = ANY(%s::text[]))
          AND NOT EXISTS (SELECT 1 FROM aggregated_claims ac WHERE ac.politician_id = p.id)
          AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.politician_id = p.id)
        """,
        (person_ids, person_ids),
    ).rowcount
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export-dir", type=Path, help="socialclaims export, e.g. var/socialclaims/claims/MX")
    parser.add_argument("--politicians", help="comma-separated person ids (default: every politician)")
    parser.add_argument("--top", type=int, default=10, help="storylines per politician, most statements first")
    parser.add_argument("--min-statements", type=int, default=2)
    parser.add_argument(
        "--comments-database-url-env",
        default="SOURCE_DATABASE_URL",
        help="env var holding System B's database URL; comments are skipped when unset",
    )
    parser.add_argument("--comments-per-post", type=int, default=20)
    parser.add_argument("--max-comments", type=int, default=40, help="per storyline")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    parser.add_argument("--remove", action="store_true", help="delete storyline-linked rows and their poll results")
    args = parser.parse_args(argv)
    person_ids = [p.strip() for p in args.politicians.split(",") if p.strip()] if args.politicians else None

    settings = Settings.from_env()
    if not args.dry_run and not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    if args.remove:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            print(json.dumps(remove_links(conn, person_ids)))
        return
    if args.export_dir is None:
        raise SystemExit("--export-dir is required")
    plans = plan_storylines(
        read_jsonl(args.export_dir / "claims.jsonl"),
        json.loads((args.export_dir / "storylines.json").read_text(encoding="utf-8")),
        person_ids,
        args.top,
        args.min_statements,
    )
    comments: dict[str, list[str]] = {}
    source_url = os.environ.get(args.comments_database_url_env)
    if source_url and not args.dry_run:
        oriane_ids = sorted({c["oriane_id"] for p in plans for c in p.claims if c["oriane_id"]})
        comments = fetch_comments(source_url, oriane_ids, args.comments_per_post)
    if args.dry_run:
        for plan in plans:
            print(f"{plan.person_id}\t{plan.storyline_id}\t{len(plan.claims)}\t{plan.canonical_text}")
        return
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        results = [(plan, link_plan(conn, plan, comments, args.max_comments)) for plan in plans]
    for plan, result in results:
        print(
            f"{plan.person_id}\t{plan.storyline_id}\t{result['aggregated_claim_id']}\t{result['claims']} claims"
            f"\t+{result['reactions_added']} comments\t{plan.canonical_text}"
        )
    print("--claims " + ",".join(r["aggregated_claim_id"] for _, r in results))


if __name__ == "__main__":
    main()
