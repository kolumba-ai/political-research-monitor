"""Phase 1: licensed Oriane capture for one politician → immutable raw snapshots, posts, metrics observations."""
from __future__ import annotations

import hashlib
import uuid
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from .config import ORIANE_CREDITS_PER_ROW, SocialSettings
from .identity import current_ref, load_terms, merged_ids, relevance_context, sync_ref
from .oriane import (CreditsExhausted, InvalidQuery, OffsetPastEnd, OrianeClient, OrianeError, OrianeResponse,
                     credits_for)
from .query import BRANCHES, canonical_json, request_body
from .relevance import bucket, score
from .systema import GraphReader, read_person

SORT = "publishedAt:desc"


class NotTrackable(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def sha256_json(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def month_credits_used(conn, now: datetime) -> int:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute("SELECT COALESCE(SUM(credits), 0) AS c FROM oriane_usage WHERE called_at >= %s", (start,)).fetchone()
    return int(row["c"])


def record_usage(conn, run_id, endpoint, purpose, projection, status, request_id, rows, credits, now) -> None:
    """A request_id is billed once: replaying a stored response records it again with 0 credits."""
    if request_id and credits and conn.execute("SELECT 1 FROM oriane_usage WHERE request_id = %s AND credits > 0",
                                               (request_id,)).fetchone():
        credits = 0
    conn.execute("INSERT INTO oriane_usage(run_id, endpoint, purpose, projection, http_status, request_id, rows, "
                 "credits, called_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (run_id, endpoint, purpose, projection, status, request_id, rows, credits, now))


def captured_ids(conn, politician_id: str) -> list[str]:
    """Posts already captured for this politician, newest first (newest are the likeliest to be re-returned)."""
    return [r["oriane_id"] for r in conn.execute(
        "SELECT p.oriane_id FROM post p WHERE EXISTS (SELECT 1 FROM run_post rp WHERE rp.oriane_id = p.oriane_id "
        "AND rp.politician_id = ANY(%s)) ORDER BY p.published_at DESC, p.oriane_id",
        (merged_ids(conn, politician_id),)).fetchall()]


def _minimize(result: dict, excluded: list[str]) -> dict:
    return {k: v for k, v in result.items() if k not in excluded}


def _post_url(r: dict) -> str | None:
    if r.get("platform") == "tiktok" and r.get("profileHandle") and r.get("platformId"):
        return f"https://www.tiktok.com/@{r['profileHandle']}/video/{r['platformId']}"
    return None


class Capturer:
    def __init__(self, conn, graph: GraphReader, client: OrianeClient | None, settings: SocialSettings, clock=utcnow):
        self.conn, self.graph, self.client, self.s, self.clock = conn, graph, client, settings, clock
        self.usage_purpose = "capture"
        self._run_id: str | None = None

    @property
    def platforms(self) -> list[str]:
        return self.s.csv(self.s.platforms)

    def plan(self, politician_id: str, *, date_from: str | None = None, date_to: str | None = None,
             max_posts: int | None = None, allow_review: bool = False) -> dict:
        """Sync the identity snapshot and build the exact request, without calling Oriane."""
        sync = sync_ref(self.conn, self.graph, politician_id, self.platforms)
        pid = sync["politician_id"]
        ref = current_ref(self.conn, pid)
        if ref is None or sync["status"] == "missing_in_system_a":
            raise NotTrackable(f"{pid} is not a System A Person")
        allowed = {"trackable"} | ({"needs_review"} if allow_review else set())
        if ref["tracking_status"] not in allowed:
            raise NotTrackable(f"{pid} is {ref['tracking_status']} (needs a current verified/official_single holding)")
        today = self.clock().date()
        d_to = date_to or today.isoformat()
        d_from = date_from or (date.fromisoformat(d_to) - timedelta(days=self.s.default_window_days)).isoformat()
        excl = captured_ids(self.conn, pid)
        body, n_excl = request_body(ref["query_json"], d_from, d_to, excl, self.s.oriane_max_filter_values)
        cap = max_posts or self.s.max_posts_per_politician
        per = ORIANE_CREDITS_PER_ROW[self.s.oriane_projection]
        return {"politician_id": pid, "ref": ref, "body": body, "date_from": d_from, "date_to": d_to,
                "excluded_ids": n_excl, "already_captured": len(excl), "max_posts": cap,
                "projection": self.s.oriane_projection, "query_id": ref["query_id"],
                "worst_case_credits": cap * per,
                "month_credits_used": month_credits_used(self.conn, self.clock()),
                "monthly_ceiling": self.s.oriane_monthly_credit_ceiling}

    # ------------------------------------------------------------------------------------------------------------
    def capture(self, politician_id: str, **kw) -> dict:
        self._run_id = None
        try:
            return self._capture(politician_id, **kw)
        except Exception as exc:
            if self._run_id:
                with self.conn.transaction():
                    self.conn.execute("UPDATE run SET status = 'error', error = %s, finished_at = %s "
                                      "WHERE run_id = %s AND finished_at IS NULL",
                                      (f"{type(exc).__name__}: {exc}", self.clock(), self._run_id))
            raise

    def _capture(self, politician_id: str, **kw) -> dict:
        if self.client is None:
            raise RuntimeError("an Oriane client is required to capture")
        p = self.plan(politician_id, **kw)
        pid, ref, body, projection = p["politician_id"], p["ref"], p["body"], p["projection"]
        per = ORIANE_CREDITS_PER_ROW[projection]
        snap = read_person(self.graph, pid)
        ctx = relevance_context(snap, load_terms(self.conn, pid))
        started = self.clock()
        run_id = f"oriane-{started.strftime('%Y%m%dT%H%M%S%fZ')}-{hashlib.sha256(pid.encode()).hexdigest()[:8]}-{uuid.uuid4().hex[:6]}"
        excluded_fields = self.s.csv(self.s.excluded_fields)
        manifest = {"excluded_fields": excluded_fields, "store_raw_payloads": self.s.store_raw_payloads,
                    "raw_retention_days": self.s.raw_retention_days, "sort": SORT, "pages": []}
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO run(run_id, politician_id, person_updated_at, politician_version, graph_run, pipeline_mode, "
                "query_id, query_json, date_from, date_to, platforms, projection, max_posts, excluded_ids, manifest, "
                "started_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, pid, ref["person_updated_at"], ref["version"], ref["graph_run"], ref["pipeline_mode"],
                 ref["query_id"], Jsonb(ref["query_json"]), p["date_from"], p["date_to"], self.platforms, projection,
                 p["max_posts"], p["excluded_ids"], Jsonb(manifest), started))
        self._run_id = run_id

        offset, rows, credits, status, error, partial = 0, 0, 0, "ok", None, False
        request_ids: list[str] = []
        branch_counts = {b: 0 for b in BRANCHES}
        aggregations, total = None, None
        while True:
            now = self.clock()
            month_left = self.s.oriane_monthly_credit_ceiling - month_credits_used(self.conn, now)
            run_left = self.s.oriane_run_credit_ceiling - credits
            limit = min(self.s.oriane_page_size, 100, p["max_posts"] - rows, min(month_left, run_left) // per)
            if p["max_posts"] - rows <= 0:
                break
            if limit < 1:
                status = "budget_ceiling"
                break
            params = {"offset": offset, "limit": limit, "projection": projection, "sort": SORT}
            try:
                resp = self.client.search_contents(body, offset=offset, limit=limit, projection=projection, sort=SORT)
            except (CreditsExhausted, OffsetPastEnd, InvalidQuery, OrianeError) as exc:
                with self.conn.transaction():
                    record_usage(self.conn, run_id, "/rest/contents/search", self.usage_purpose, projection, exc.status,
                                 exc.request_id, 0, 0, now)
                if exc.request_id:
                    request_ids.append(exc.request_id)
                if isinstance(exc, OffsetPastEnd):
                    break
                status = {CreditsExhausted: "credits_exhausted", InvalidQuery: "invalid_query"}.get(type(exc), "error")
                error = str(exc)
                break
            results = resp.results
            if resp.request_id:
                request_ids.append(resp.request_id)
            if aggregations is None:
                aggregations = (resp.body.get("data") or {}).get("aggregations")
            total = resp.pagination.get("totalCount", total)
            partial = partial or resp.partial
            # Spend is committed before processing so a later failure cannot lose billed rows.
            with self.conn.transaction():
                record_usage(self.conn, run_id, "/rest/contents/search", self.usage_purpose, projection, resp.status,
                             resp.request_id, len(results), credits_for(len(results), projection), now)
            with self.conn.transaction():
                page = self._store_page(run_id, pid, ref, resp, params, body, now, excluded_fields, ctx, branch_counts)
            manifest["pages"].append(page)
            rows += len(results)
            credits += credits_for(len(results), projection)
            offset += len(results)
            if not results or len(results) < limit or (total is not None and offset >= total):
                break

        if status == "ok" and partial:
            status = "partial"
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE run SET request_ids = %s, aggregations = %s, total_count = %s, next_offset = %s, "
                "rows_returned = %s, credits_used = %s, branch_counts = %s, status = %s, error = %s, manifest = %s, "
                "finished_at = %s WHERE run_id = %s",
                (request_ids, Jsonb(aggregations) if aggregations is not None else None, total, offset, rows, credits,
                 Jsonb(branch_counts), status, error, Jsonb(manifest), self.clock(), run_id))
        return {"run_id": run_id, "politician_id": pid, "status": status, "rows": rows, "credits": credits,
                "total_count": total, "branch_counts": branch_counts, "excluded_ids": p["excluded_ids"],
                "error": error}

    # ------------------------------------------------------------------------------------------------------------
    def _store_page(self, run_id, pid, ref, resp, params, body, now, excluded_fields, ctx, branch_counts) -> dict:
        results = [_minimize(r, excluded_fields) for r in resp.results]
        response = json.loads(json.dumps(resp.body))
        if "data" in response and isinstance(response["data"], dict):
            response["data"]["results"] = results
        envelope = {"request": {"method": "POST", "path": "/rest/contents/search", "params": params, "body": body},
                    "status": resp.status, "captured_at": now.isoformat(), "response": response}
        page_sha = sha256_json(envelope)
        rel_path = f"{run_id}/page-{params['offset']:06d}.json"
        if self.s.store_raw_payloads:
            path = Path(self.s.raw_storage_dir) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(envelope, ensure_ascii=False, indent=1, sort_keys=True))
            path.chmod(0o600)
        retain_until = now + timedelta(days=self.s.raw_retention_days) if self.s.raw_retention_days > 0 else None
        skipped = 0
        for i, r in enumerate(results):
            oid = r.get("id") or f"{r.get('platform')}:{r.get('platformId')}"
            published = _ts(r.get("publishedAt"))
            if published is None:
                skipped += 1
                self.conn.execute("INSERT INTO review(item_type, item_id, reason, payload) VALUES ('post', %s, "
                                  "'missing_published_at', %s) ON CONFLICT DO NOTHING", (oid, Jsonb({"run_id": run_id})))
                continue
            matched = sorted({m.split(":")[0] for m in (r.get("matchedQueries") or []) if m.split(":")[0] in BRANCHES})
            for m in matched:
                branch_counts[m] += 1
            raw_id = "raw:" + hashlib.sha256(f"{run_id}|{oid}".encode()).hexdigest()[:20]
            self.conn.execute(
                "INSERT INTO raw_data(raw_id, run_id, oriane_id, platform, projection, request_id, payload_path, "
                "payload_sha256, excluded_fields, pipeline_mode, published_at, oriane_created_at, oriane_updated_at, "
                "captured_at, retain_until) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (raw_id) DO NOTHING",
                (raw_id, run_id, oid, r.get("platform"), params["projection"], resp.request_id,
                 f"{rel_path}#/response/data/results/{i}" if self.s.store_raw_payloads else None, sha256_json(r),
                 excluded_fields, ref["pipeline_mode"], published, _ts(r.get("createdAt")), _ts(r.get("updatedAt")),
                 now, retain_until))
            rel, basis = score(r, matched, ctx)
            langs = sorted({x for x in [r.get("captionLanguage"), *[m.get("transcriptLanguage")
                                                                     for m in r.get("media") or []]] if x})
            audio = r.get("audio") or {}
            self.conn.execute(
                "INSERT INTO post(oriane_id, platform, platform_id, format, url, profile_id, profile_handle, caption, "
                "hashtags, languages, location_country_code, audio_platform_id, matched_queries, relevance_score, "
                "pipeline_mode, latest_raw_id, published_at, first_observed_at, last_captured_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (oriane_id) DO UPDATE SET "
                "matched_queries = ARRAY(SELECT DISTINCT unnest(post.matched_queries || EXCLUDED.matched_queries) ORDER BY 1), "
                "relevance_score = GREATEST(post.relevance_score, EXCLUDED.relevance_score), "
                "caption = EXCLUDED.caption, hashtags = EXCLUDED.hashtags, languages = EXCLUDED.languages, "
                "location_country_code = EXCLUDED.location_country_code, profile_handle = EXCLUDED.profile_handle, "
                "pipeline_mode = EXCLUDED.pipeline_mode, latest_raw_id = EXCLUDED.latest_raw_id, "
                "last_captured_at = EXCLUDED.last_captured_at",
                (oid, r.get("platform"), r.get("platformId"), r.get("format"), _post_url(r), r.get("profileId"),
                 r.get("profileHandle"), r.get("caption"), list(r.get("hashtags") or []), langs,
                 r.get("locationCountryCode"), audio.get("platformId") if isinstance(audio, dict) else None,
                 matched, rel, ref["pipeline_mode"], raw_id, published, now, now))
            self.conn.execute(
                "INSERT INTO run_post(run_id, oriane_id, politician_id, matched_queries, relevance_score, relevance, "
                "relevance_basis, raw_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (run_id, oid, pid, matched, rel,
                 bucket(rel, self.s.relevance_keep_threshold, self.s.relevance_review_threshold), Jsonb(basis), raw_id))
            self.conn.execute(
                "INSERT INTO post_metrics(oriane_id, captured_at, raw_id, views, likes, shares, comments, interactions, "
                "engagement_rate_per_views, engagement_rate_per_followers, views_rate_per_followers, post_age_days) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (oid, now, raw_id, r.get("viewsCount"), r.get("likesCount"), r.get("sharesCount"),
                 r.get("commentsCount"), r.get("interactionsCount"), r.get("engagementRatePerViews"),
                 r.get("engagementRatePerFollowers"), r.get("viewsRatePerFollowers"),
                 round((now - published).total_seconds() / 86400, 3)))
        return {"offset": params["offset"], "limit": params["limit"], "status": resp.status,
                "request_id": resp.request_id, "rows": len(results), "skipped_missing_published_at": skipped,
                "path": rel_path if self.s.store_raw_payloads else None, "sha256": page_sha}


class RawReplayClient:
    """Serves the pages a failed capture already saved, so billed rows are ingested without calling Oriane again.
    Refuses if the rebuilt request differs from the stored one."""

    def __init__(self, run_dir: Path):
        self.pages = {}
        for f in sorted(Path(run_dir).glob("page-*.json")):
            env = json.loads(f.read_text())
            self.pages[env["request"]["params"]["offset"]] = env
        if not self.pages:
            raise ValueError(f"no stored pages in {run_dir}")
        first = self.pages[min(self.pages)]
        self.body = first["request"]["body"]
        self.captured_at = datetime.fromisoformat(first["captured_at"])
        self.rows = sum(len((e["response"].get("data") or {}).get("results") or []) for e in self.pages.values())
        # Reproduces the original per-page limits when passed as max_posts.
        self.max_posts = sum(e["request"]["params"]["limit"] for e in self.pages.values())

    def search_contents(self, body, *, offset, limit, projection, sort) -> OrianeResponse:
        env = self.pages.get(offset)
        if env is None:
            raise ValueError(f"no stored page at offset {offset}")
        params = env["request"]["params"]
        if canonical_json(body) != canonical_json(env["request"]["body"]) or \
                (limit, projection, sort) != (params["limit"], params["projection"], params["sort"]):
            raise ValueError("rebuilt request differs from the stored page; refusing to replay")
        return OrianeResponse(env["status"], env["response"], (env["response"].get("metadata") or {}).get("requestId"))


def purge_expired(conn, settings: SocialSettings, now: datetime | None = None) -> int:
    """Delete raw page files whose retention has lapsed; hashes and row metadata are kept for audit."""
    now = now or utcnow()
    rows = conn.execute("SELECT raw_id, payload_path FROM raw_data WHERE retain_until IS NOT NULL "
                        "AND retain_until < %s AND purged_at IS NULL", (now,)).fetchall()
    files = {r["payload_path"].split("#")[0] for r in rows if r["payload_path"]}
    with conn.transaction():
        for f in files:
            (Path(settings.raw_storage_dir) / f).unlink(missing_ok=True)
        conn.execute("UPDATE raw_data SET purged_at = %s, payload_path = NULL WHERE raw_id = ANY(%s)",
                     (now, [r["raw_id"] for r in rows]))
    return len(rows)
