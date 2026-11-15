"""Read-only view of the claims pipeline: Systems B and C's Postgres (capture -> claims -> evidence -> past.dev).

Every connection is opened with `default_transaction_read_only=on`; nothing here writes to Postgres or calls past.dev.
The unit followed through the pipeline is a *record*: one proposition (distinct claim) for one politician, which is
what System C sends to past.dev as `clm-<hash>-<person slug>`.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException

MAX_LIMIT = 200
CACHE_SECONDS = 60

# Furthest step each record has reached, in pipeline order, with what it means.
STAGES = [
    ("not_eligible", "Not eligible for checking", "The quote could not be matched word for word in the post, or the claim is a question or satire."),
    ("waiting_for_intake", "Waiting for intake", "Eligible, but not yet picked up by the evidence intake."),
    ("skipped", "Skipped at intake", "Not check-worthy enough, not verifiable now, or a commitment not yet due."),
    ("waiting_for_search", "Waiting for evidence search", "Queued; no evidence has been searched yet."),
    ("searching", "Evidence search running", "A worker is searching, fetching and judging pages right now."),
    ("no_evidence", "Checked, no evidence found", "Searched, but no page was fetched and judged."),
    ("no_usable_evidence", "Checked, no usable evidence",
     "Pages were judged, but none had a verified quote and a stance other than irrelevant."),
    ("waiting_to_send", "Evidence ready, not sent yet", "Has usable evidence; waiting for the past.dev sync."),
    ("send_failed", "Send to past.dev failed", "past.dev rejected the record; it will be resent."),
    ("sent_unconfirmed", "Sent, past.dev still processing", "Sent; past.dev has not confirmed indexing yet."),
    ("in_pastdev", "In past.dev", "Sent and confirmed by past.dev."),
]
STAGE_KEYS = [s[0] for s in STAGES]

# One row per proposition x politician from each post's latest successful extraction, with its furthest stage.
RECORDS = """
WITH latest AS (
    SELECT DISTINCT ON (oriane_id, politician_id) oriane_id, politician_id, raw_id, extractor_version
    FROM claim_extraction WHERE status = 'ok' AND politician_id LIKE %(pfx)s
    ORDER BY oriane_id, politician_id, extracted_at DESC
), cur AS (
    SELECT d.* FROM distilled_claim d
    JOIN latest l ON l.oriane_id = d.oriane_id AND l.politician_id = d.politician_id
                 AND l.raw_id = d.raw_id AND l.extractor_version = d.extractor_version
), pp AS (
    SELECT politician_id, proposition_id, count(*) AS claims, count(DISTINCT oriane_id) AS posts,
           min(stated_at) AS first_stated_at, max(checkworthiness) AS checkworthiness,
           array_agg(DISTINCT type) AS types, array_agg(claim_id) AS claim_ids,
           bool_or(grounding = 'exact' AND modality IN ('asserted', 'quoted')) AS groundable
    FROM cur WHERE proposition_id IS NOT NULL GROUP BY 1, 2
), ev AS (
    SELECT proposition_id, count(*) AS judged,
           count(*) FILTER (WHERE quote_verified AND stance <> 'irrelevant') AS usable,
           count(*) FILTER (WHERE quote_verified AND stance = 'supports') AS supports,
           count(*) FILTER (WHERE quote_verified AND stance = 'contradicts') AS contradicts,
           count(*) FILTER (WHERE quote_verified AND stance = 'context') AS context
    FROM ev_evidence_link GROUP BY 1
), rec AS (
    SELECT pp.*, q.status AS queue_status, CASE WHEN q.status = 'skipped' THEN q.skip_reason END AS skip_reason,
           q.priority, q.reach,
           COALESCE(ev.judged, 0) AS judged, COALESCE(ev.usable, 0) AS usable, COALESCE(ev.supports, 0) AS supports,
           COALESCE(ev.contradicts, 0) AS contradicts, COALESCE(ev.context, 0) AS context,
           m.datapoint_id, m.status AS sent_status, m.sent_at, m.completed_at, m.error AS send_error,
           CASE WHEN m.status = 'completed' THEN 'in_pastdev'
                WHEN m.status = 'queued' THEN 'sent_unconfirmed'
                WHEN m.status = 'failed' THEN 'send_failed'
                WHEN COALESCE(ev.usable, 0) > 0 THEN 'waiting_to_send'
                WHEN COALESCE(ev.judged, 0) > 0 THEN 'no_usable_evidence'
                WHEN q.status = 'mapped' OR q.skip_reason = 'pastdev_pending' THEN 'no_evidence'
                WHEN q.status = 'mapping' THEN 'searching'
                WHEN q.status = 'queued' THEN 'waiting_for_search'
                WHEN q.status = 'skipped' THEN 'skipped'
                WHEN pp.groundable THEN 'waiting_for_intake'
                ELSE 'not_eligible' END AS stage
    FROM pp
    LEFT JOIN ev_proposition_queue q ON q.proposition_id = pp.proposition_id
    LEFT JOIN ev ON ev.proposition_id = pp.proposition_id
    LEFT JOIN ev_memory_record m ON m.kind = 'claim' AND m.proposition_id = pp.proposition_id
                                AND m.politician_id = pp.politician_id AND m.deleted_at IS NULL
)
"""


def _plain(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return float(v) if type(v).__name__ == "Decimal" else v


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    return [{k: _plain(v) for k, v in r.items()} for r in conn.execute(sql, params or {})]


def _export_claim_ids(root: Path, country: str) -> tuple[set[str] | None, dict]:
    d = Path(root) / country
    if not (d / "claims.jsonl").exists():
        return None, {}
    ids = {json.loads(line).get("claim_id") for line in (d / "claims.jsonl").read_text("utf-8").split("\n") if line.strip()}
    manifest = json.loads((d / "manifest.json").read_text("utf-8")) if (d / "manifest.json").exists() else {}
    return ids, manifest


def build_router(get_url: Callable[[], str | None], get_export_dir: Callable[[], Path],
                 connect: Callable[[str], object] | None = None) -> APIRouter:
    r = APIRouter()
    cache: dict[tuple, tuple[float, dict]] = {}

    def db():
        url = get_url()
        if not url:
            return None
        if connect:
            return connect(url)
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=20, options="-c default_transaction_read_only=on")
        conn.read_only = True  # also holds behind a connection pooler, which may drop startup options
        conn.execute("SET LOCAL statement_timeout = 60000")
        return conn

    def cached(key: tuple, build: Callable[[object], dict]) -> dict:
        hit = cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        conn = None
        try:
            conn = db()
            if conn is None:
                return {"available": False, "reason": "PIPELINE_DATABASE_URL is not set"}
            out = build(conn)
        except HTTPException:
            raise
        except Exception as exc:  # database unreachable or schema mismatch: say so instead of a 500, without the URL
            if type(exc).__name__ == "OperationalError":  # its message can name the host
                return {"available": False, "reason": "could not connect to the pipeline database"}
            return {"available": False, "reason": f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:200]}"}
        finally:
            if conn is not None:
                conn.close()
        cache[key] = (time.monotonic(), out)
        return out

    @r.get("/api/pipeline/{code}/summary")
    def summary(code: str):
        country = code.upper()
        p = {"pfx": f"person:{country}:%"}

        def build(conn) -> dict:
            names = {x["politician_id"]: x["name"] for x in _rows(
                conn, "SELECT politician_id, name FROM politician_ref WHERE superseded_at IS NULL AND politician_id LIKE %(pfx)s", p)}
            per = _rows(conn, RECORDS + """
                SELECT politician_id, stage, count(*) AS records, sum(claims) AS claims FROM rec GROUP BY 1, 2""", p)
            posts = {x["politician_id"]: x for x in _rows(conn, """
                SELECT rp.politician_id, count(DISTINCT rp.oriane_id) AS captured,
                       count(DISTINCT ce.oriane_id) AS extracted
                FROM run_post rp LEFT JOIN claim_extraction ce
                  ON ce.oriane_id = rp.oriane_id AND ce.politician_id = rp.politician_id AND ce.status = 'ok'
                WHERE rp.politician_id LIKE %(pfx)s GROUP BY 1""", p)}
            pols: dict[str, dict] = {}
            for pid in set(names) | set(posts) | {x["politician_id"] for x in per}:
                pols[pid] = {"politician_id": pid, "name": names.get(pid), "posts_captured": (posts.get(pid) or {}).get("captured", 0),
                             "posts_extracted": (posts.get(pid) or {}).get("extracted", 0), "claims": 0, "records": 0,
                             "stages": {k: 0 for k in STAGE_KEYS}}
            for x in per:
                pol = pols[x["politician_id"]]
                pol["stages"][x["stage"]] = x["records"]
                pol["records"] += x["records"]
                pol["claims"] += int(x["claims"])
            stages = {k: sum(pol["stages"][k] for pol in pols.values()) for k in STAGE_KEYS}
            after = lambda key: sum(stages[k] for k in STAGE_KEYS[STAGE_KEYS.index(key):])  # noqa: E731
            totals = _rows(conn, """
                SELECT (SELECT count(DISTINCT oriane_id) FROM run_post WHERE politician_id LIKE %(pfx)s) AS posts_captured,
                       (SELECT count(DISTINCT oriane_id) FROM claim_extraction WHERE status = 'ok' AND politician_id LIKE %(pfx)s) AS posts_extracted""", p)[0]
            funnel = [
                {"key": "posts_captured", "label": "Posts captured from Oriane", "count": totals["posts_captured"]},
                {"key": "posts_extracted", "label": "Posts with claims extracted", "count": totals["posts_extracted"]},
                {"key": "claims", "label": "Claims extracted", "count": sum(pol["claims"] for pol in pols.values())},
                {"key": "records", "label": "Distinct claims per politician", "count": sum(stages.values())},
                {"key": "eligible", "label": "Eligible for evidence check", "count": after("waiting_for_intake")},
                {"key": "queued", "label": "Accepted for evidence search", "count": after("waiting_for_search")},
                {"key": "checked", "label": "Evidence searched", "count": after("no_evidence")},
                {"key": "usable", "label": "With usable evidence", "count": after("waiting_to_send")},
                {"key": "sent", "label": "Sent to past.dev", "count": after("send_failed")},
                {"key": "confirmed", "label": "Confirmed by past.dev", "count": stages["in_pastdev"]},
            ]
            last = _rows(conn, """
                SELECT (SELECT max(finished_at) FROM run) AS capture, (SELECT max(extracted_at) FROM claim_extraction) AS extraction,
                       (SELECT max(requested_at) FROM ev_exa_request) AS search, (SELECT max(assessed_at) FROM ev_evidence_link) AS judgement,
                       (SELECT max(sent_at) FROM ev_memory_record) AS sent, (SELECT max(completed_at) FROM ev_memory_record) AS confirmed""")[0]
            hourly: dict[str, dict] = {}
            for x in _rows(conn, """
                SELECT 'extracted' AS k, date_trunc('hour', extracted_at) AS h, sum(claims) AS n FROM claim_extraction
                  WHERE status = 'ok' AND extracted_at > now() - interval '48 hours' GROUP BY 2
                UNION ALL SELECT 'judged', date_trunc('hour', assessed_at), count(*) FROM ev_evidence_link
                  WHERE assessed_at > now() - interval '48 hours' GROUP BY 2
                UNION ALL SELECT 'sent', date_trunc('hour', sent_at), count(*) FROM ev_memory_record
                  WHERE sent_at > now() - interval '48 hours' GROUP BY 2
                UNION ALL SELECT 'confirmed', date_trunc('hour', completed_at), count(*) FROM ev_memory_record
                  WHERE completed_at > now() - interval '48 hours' GROUP BY 2"""):
                hourly.setdefault(x["h"], {"hour": x["h"], "extracted": 0, "judged": 0, "sent": 0, "confirmed": 0})[x["k"]] = int(x["n"])
            spend = _rows(conn, """
                SELECT (SELECT COALESCE(sum(cost_usd), 0) FROM ev_exa_request) AS exa_usd, (SELECT count(*) FROM ev_exa_request) AS exa_requests,
                       (SELECT COALESCE(sum(credits), 0) FROM oriane_usage) AS oriane_credits,
                       (SELECT COALESCE(sum(content_bytes), 0) FROM ev_memory_record) AS pastdev_bytes""")[0]
            sent_ids = {c for x in _rows(conn, RECORDS + "SELECT claim_ids FROM rec WHERE datapoint_id IS NOT NULL", p)
                        for c in x["claim_ids"]}
            export_ids, manifest = _export_claim_ids(get_export_dir(), country)
            recon = {"neon_sent_claims": len(sent_ids), "explorer_available": export_ids is not None,
                     "explorer_source": manifest.get("source"), "explorer_pulled_at": manifest.get("pulled_at")}
            if export_ids is not None:
                recon.update({"explorer_claims": len(export_ids), "in_both": len(sent_ids & export_ids),
                              "sent_not_in_explorer": len(sent_ids - export_ids),
                              "in_explorer_not_in_log": len(export_ids - sent_ids),
                              "explorer_evidence_records": (manifest.get("skipped") or {}).get("evidence_records")})
            return {
                "country": country, "available": True, "generated_at": _plain(datetime.now(timezone.utc)),
                "stage_info": [{"key": k, "label": lbl, "meaning": m} for k, lbl, m in STAGES],
                "funnel": funnel, "stages": stages, "last": last, "spend": spend, "reconciliation": recon,
                "hourly": [hourly[h] for h in sorted(hourly)],
                "skip_reasons": _rows(conn, RECORDS + "SELECT COALESCE(skip_reason, 'unknown') AS reason, count(*) AS records FROM rec WHERE stage = 'skipped' GROUP BY 1 ORDER BY 2 DESC", p),
                "evidence": _rows(conn, "SELECT stance, quote_verified, count(*) AS links, count(DISTINCT proposition_id) AS claims FROM ev_evidence_link GROUP BY 1, 2 ORDER BY 3 DESC"),
                "fetch": _rows(conn, "SELECT status, count(*) AS urls FROM ev_fetch_queue GROUP BY 1 ORDER BY 2 DESC"),
                "fetch_errors": _rows(conn, "SELECT status, left(last_error, 80) AS error, count(*) AS urls FROM ev_fetch_queue WHERE last_error IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12"),
                "sent": _rows(conn, "SELECT kind, status, count(*) AS records, sum(content_bytes) AS bytes, max(sent_at) AS last_sent, max(completed_at) AS last_confirmed, count(error) AS errors FROM ev_memory_record WHERE deleted_at IS NULL GROUP BY 1, 2 ORDER BY 1, 2"),
                "capture_runs": _rows(conn, "SELECT status, count(*) AS runs, max(started_at) AS last FROM run GROUP BY 1 ORDER BY 2 DESC"),
                "extraction": _rows(conn, "SELECT status, count(*) AS extractions, max(extracted_at) AS last FROM claim_extraction GROUP BY 1 ORDER BY 2 DESC"),
                "politicians": sorted(pols.values(), key=lambda x: (-x["records"], x["name"] or "")),
            }
        return cached(("summary", country), build)

    @r.get("/api/pipeline/{code}/records")
    def records(code: str, politician_id: str | None = None, stage: str | None = None, q: str | None = None,
                limit: int = 50, offset: int = 0):
        if limit < 1 or limit > MAX_LIMIT or offset < 0:
            raise HTTPException(422, f"limit must be 1..{MAX_LIMIT} and offset >= 0")
        if stage and stage not in STAGE_KEYS:
            raise HTTPException(422, f"stage must be one of {STAGE_KEYS}")
        country = code.upper()
        p = {"pfx": f"person:{country}:%", "pid": politician_id, "stage": stage, "q": f"%{q}%" if q else None,
             "limit": limit, "offset": offset}
        where = """ FROM rec JOIN proposition pr ON pr.proposition_id = rec.proposition_id
                    WHERE (%(pid)s::text IS NULL OR rec.politician_id = %(pid)s)
                      AND (%(stage)s::text IS NULL OR rec.stage = %(stage)s)
                      AND (%(q)s::text IS NULL OR pr.canonical_text ILIKE %(q)s)"""

        def build(conn) -> dict:
            total = _rows(conn, RECORDS + "SELECT count(*) AS n" + where, p)[0]["n"]
            rows = _rows(conn, RECORDS + """
                SELECT rec.politician_id, rec.proposition_id, pr.canonical_text, rec.stage, rec.skip_reason, rec.claims,
                       rec.posts, rec.types, rec.first_stated_at, rec.checkworthiness, rec.priority, rec.reach, rec.judged,
                       rec.usable, rec.supports, rec.contradicts, rec.context, rec.datapoint_id, rec.sent_at, rec.completed_at
                """ + where + " ORDER BY rec.priority DESC NULLS LAST, rec.first_stated_at DESC LIMIT %(limit)s OFFSET %(offset)s", p)
            return {"country": country, "available": True, "total": total, "offset": offset, "limit": limit, "records": rows}
        return cached(("records", country, politician_id, stage, q, limit, offset), build)

    @r.get("/api/pipeline/{code}/trace/{proposition_id}")
    def trace(code: str, proposition_id: str, politician_id: str | None = None):
        country = code.upper()
        p = {"pfx": f"person:{country}:%", "prop": proposition_id, "pid": politician_id}

        def build(conn) -> dict:
            prop = _rows(conn, "SELECT proposition_id, canonical_text, subject, predicate, negated, place, period_start, period_end, first_stated_at FROM proposition WHERE proposition_id = %(prop)s", p)
            if not prop:
                raise HTTPException(404, "unknown proposition")
            recs = _rows(conn, RECORDS + "SELECT politician_id, stage, skip_reason, datapoint_id, sent_status, sent_at, completed_at, send_error, claim_ids FROM rec WHERE proposition_id = %(prop)s AND (%(pid)s::text IS NULL OR politician_id = %(pid)s)", p)
            export_ids, _ = _export_claim_ids(get_export_dir(), country)
            for x in recs:
                x["in_explorer"] = None if export_ids is None else any(c in export_ids for c in x.pop("claim_ids"))
            claims = _rows(conn, """
                SELECT d.claim_id, d.politician_id, d.oriane_id, d.quote, d.text, d.text_en, d.type, d.relation_to_politician,
                       d.speaker_status, d.modality, d.grounding, d.grounding_issues, d.checkworthiness, d.stated_at,
                       d.extractor_version, d.extracted_at, po.platform, po.url, po.profile_handle
                FROM distilled_claim d JOIN post po ON po.oriane_id = d.oriane_id
                WHERE d.proposition_id = %(prop)s AND (%(pid)s::text IS NULL OR d.politician_id = %(pid)s)
                ORDER BY d.extracted_at DESC, d.stated_at""", p)
            queue = _rows(conn, "SELECT status, skip_reason, max_checkworthiness, reach, priority, claim_count, claim_types, mapper_version, queued_at, mapped_at, updated_at FROM ev_proposition_queue WHERE proposition_id = %(prop)s", p)
            searches = _rows(conn, """
                SELECT r.request_id, r.purpose, r.params->>'query' AS query, r.params->'includeDomains' AS domains,
                       r.result_count, r.cost_usd, r.latency_ms, r.requested_at
                FROM ev_exa_request r WHERE r.proposition_id = %(prop)s ORDER BY r.requested_at""", p)
            urls = _rows(conn, """
                SELECT u.request_id, u.rank, u.url, u.title, f.status AS fetch_status, f.attempts, f.last_error
                FROM ev_discovered_url u JOIN ev_exa_request r ON r.request_id = u.request_id
                LEFT JOIN ev_fetch_queue f ON f.url_hash = u.url_hash
                WHERE r.proposition_id = %(prop)s ORDER BY u.request_id, u.rank""", p)
            by_request: dict[str, list[dict]] = {}
            for u in urls:
                by_request.setdefault(u.pop("request_id"), []).append(u)
            for s in searches:
                s["urls"] = by_request.get(s["request_id"], [])
            links = _rows(conn, """
                SELECT l.link_id, l.query_row, l.stance, l.confidence, l.quote_verified, l.summary, l.quote, l.rationale,
                       l.model, l.mapper_version, l.assessed_at, d.document_id, d.canonical_url AS url, d.publisher_domain,
                       d.title, d.source_class, d.authority_tier, d.licence_class, d.published_at,
                       (l.quote_verified AND l.stance <> 'irrelevant') AS usable
                FROM ev_evidence_link l JOIN ev_document d ON d.document_id = l.document_id
                WHERE l.proposition_id = %(prop)s ORDER BY usable DESC, l.confidence DESC""", p)
            for x in links:
                x["not_sent_because"] = (None if x["usable"] else "stance is irrelevant" if x["stance"] == "irrelevant"
                                         else "quote was not found in the fetched page")
            sent = _rows(conn, """
                SELECT datapoint_id, kind, politician_id, document_id, audience, status, content_bytes, sent_at, completed_at, error
                FROM ev_memory_record
                WHERE deleted_at IS NULL AND (proposition_id = %(prop)s OR (kind = 'document' AND document_id IN (
                    SELECT document_id FROM ev_evidence_link WHERE proposition_id = %(prop)s)))
                  AND (%(pid)s::text IS NULL OR politician_id IS NULL OR politician_id = %(pid)s)
                ORDER BY kind, sent_at""", p)
            return {"country": country, "available": True, "proposition": prop[0], "records": recs, "claims": claims,
                    "queue": queue[0] if queue else None, "searches": searches, "evidence": links, "sent": sent,
                    "stage_info": [{"key": k, "label": lbl, "meaning": m} for k, lbl, m in STAGES]}
        return cached(("trace", country, proposition_id, politician_id), build)

    return r
