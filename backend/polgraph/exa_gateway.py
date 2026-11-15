"""Exa access layer: the only module that talks to the Exa API.

Adds what the raw SDK does not: a persistent response cache, a usage/cost ledger, per-run and
lifetime budget enforcement, client-side rate limiting, retries with exponential backoff on
429/5xx/timeouts, and resumable Agent runs (run ids are persisted so a crash or re-run polls the
existing run instead of paying for a new one).

Exa is treated as retrieval/evidence discovery only — nothing here decides what is true.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg

from .config import Settings
from .ids import canonical_url, short_hash, utcnow
from .logging_setup import get_logger

log = get_logger("exa")

# Conservative upper-bound estimates (USD) used to *reserve* budget before a call.
# Actual costs come from the API's costDollars and are what the ledger records.
EST_SEARCH = 0.015
EST_CONTENTS_PER_URL = 0.002


class BudgetExceeded(RuntimeError):
    pass


class ExaUnavailable(RuntimeError):
    pass


def _status_from_error(exc: Exception) -> int | None:
    m = re.search(r"status(?: code)? (\d{3})", str(exc))
    return int(m.group(1)) if m else None


def _retryable(exc: Exception) -> bool:
    status = _status_from_error(exc)
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "connection")) or "timed out" in str(exc).lower()


def _to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=False)
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_jsonable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _norm_result(r: Any) -> dict:
    g = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k, None))
    return {
        "url": g("url"),
        "title": g("title"),
        "published_date": g("published_date") or g("publishedDate"),
        "author": g("author"),
        "score": g("score"),
        "text": g("text"),
        "highlights": g("highlights") or [],
    }


def _cost_total(resp: Any) -> float | None:
    c = getattr(resp, "cost_dollars", None) if not isinstance(resp, dict) else resp.get("costDollars")
    if c is None:
        return None
    total = getattr(c, "total", None) if not isinstance(c, dict) else c.get("total")
    try:
        return float(total) if total is not None else None
    except (TypeError, ValueError):
        return None


class _RateLimiter:
    def __init__(self, rps: float):
        self.min_interval = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if delay > 0:
            time.sleep(delay)


class PgRateLimiter:
    def __init__(self, database_url: str, key: str, rps: float, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.database_url = database_url
        self.key = hashlib.sha256(key.encode()).hexdigest()[:16]
        self.min_interval = 1.0 / max(rps, 0.1)
        self.clock = clock
        self.sleep = sleep

    def wait(self) -> None:
        with psycopg.connect(self.database_url) as conn:
            with conn.transaction():
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS exa_rate_limit "
                    "(key TEXT PRIMARY KEY, next_at DOUBLE PRECISION NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO exa_rate_limit(key, next_at) VALUES (%s, 0) ON CONFLICT (key) DO NOTHING",
                    (self.key,),
                )
                row = conn.execute("SELECT next_at FROM exa_rate_limit WHERE key = %s FOR UPDATE",
                                   (self.key,)).fetchone()
                slot = max(self.clock(), row[0])
                conn.execute("UPDATE exa_rate_limit SET next_at = %s WHERE key = %s",
                             (slot + self.min_interval, self.key))
        delay = slot - self.clock()
        if delay > 0:
            self.sleep(delay)


@dataclass
class RunUsage:
    """Spend for the current pipeline invocation."""
    spent: float = 0.0
    reserved: float = 0.0
    calls: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0


class ExaGateway:
    def __init__(self, settings: Settings, client: Any | None = None, db_path: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep, limiter: Any | None = None,
                 budget_scope: str | None = None, monthly_budget_usd: float | None = None):
        self.settings = settings
        self._client = client
        self._sleep = sleep
        self.budget_scope = budget_scope
        self.monthly_budget_usd = monthly_budget_usd
        if budget_scope is not None and monthly_budget_usd is None:
            raise ValueError("monthly_budget_usd is required when budget_scope is set")
        self.db_path = db_path or settings.data_dir / "exa_cache.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db_lock = threading.Lock()
        self._init_db()
        if limiter is not None:
            self.limiter = limiter
        elif getattr(settings, "exa_limiter_database_url", None):
            self.limiter = PgRateLimiter(
                settings.exa_limiter_database_url, settings.exa_api_key or "",
                getattr(settings, "exa_shared_requests_per_second", 8.0),
            )
        else:
            self.limiter = _RateLimiter(settings.exa_requests_per_second)
        self.usage = RunUsage()

    # ------------------------------------------------------------------ infra
    @property
    def client(self):
        if self._client is None:
            if not self.settings.exa_api_key:
                raise ExaUnavailable("EXA_API_KEY is not set; add it to .env (see .env.example)")
            from exa_py import Exa
            self._client = Exa(api_key=self.settings.exa_api_key)
        return self._client

    def _init_db(self) -> None:
        with self._db_lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, endpoint TEXT, request TEXT,
                    response TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, endpoint TEXT,
                    purpose TEXT, cost_usd REAL, estimated INTEGER, cached INTEGER, ref TEXT);
                CREATE TABLE IF NOT EXISTS agent_runs (key TEXT PRIMARY KEY, run_id TEXT, purpose TEXT,
                    status TEXT, created_at TEXT, completed_at TEXT, cost_usd REAL);
                """
            )

    def _cache_get(self, key: str, ttl_hours: float | None) -> Any | None:
        with self._db_lock:
            row = self._db.execute("SELECT response, created_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        ttl = self.settings.cache_ttl_hours if ttl_hours is None else ttl_hours
        created = datetime.fromisoformat(row[1])
        if datetime.now(timezone.utc) - created > timedelta(hours=ttl):
            return None
        return json.loads(row[0])

    def _cache_put(self, key: str, endpoint: str, request: dict, response: Any) -> None:
        with self._db_lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                (key, endpoint, json.dumps(request, sort_keys=True), json.dumps(response), utcnow()),
            )

    def _record(self, endpoint: str, purpose: str, cost: float | None, estimate: float, cached: bool,
                ref: str = "") -> None:
        actual = 0.0 if cached else (cost if cost is not None else estimate)
        with self._db_lock, self._db:
            self._db.execute(
                "INSERT INTO usage (ts, endpoint, purpose, cost_usd, estimated, cached, ref) VALUES (?,?,?,?,?,?,?)",
                (utcnow(), endpoint, purpose, actual, int(cost is None and not cached), int(cached), ref),
            )
        if cached:
            self.usage.cache_hits += 1
        else:
            self.usage.spent += actual
            self.usage.calls[endpoint] = self.usage.calls.get(endpoint, 0) + 1

    def lifetime_spend(self) -> float:
        with self._db_lock:
            return float(self._db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM usage").fetchone()[0])

    def _reserve(self, estimate: float, what: str) -> None:
        run_left = self.settings.exa_run_budget_usd - self.usage.spent - self.usage.reserved
        if self.budget_scope is None:
            budget_left = self.settings.exa_total_budget_usd - self.lifetime_spend() - self.usage.reserved
            budget_name = "lifetime"
        else:
            now = datetime.now(timezone.utc)
            month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
            prefix = f"{self.budget_scope}:"
            with self._db_lock:
                spent = self._db.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage "
                    "WHERE ts >= ? AND substr(purpose, 1, ?) = ?",
                    (month_start, len(prefix), prefix),
                ).fetchone()[0]
            budget_left = self.monthly_budget_usd - spent - self.usage.reserved
            budget_name = "monthly"
        if estimate > run_left or estimate > budget_left:
            raise BudgetExceeded(
                f"{what}: estimated ${estimate:.3f} exceeds remaining budget "
                f"(run ${run_left:.3f}, {budget_name} ${budget_left:.3f})"
            )
        self.usage.reserved += estimate

    def _validate_purpose(self, purpose: str) -> None:
        if self.budget_scope is not None and not purpose.startswith(f"{self.budget_scope}:"):
            raise ValueError(f"purpose must start with '{self.budget_scope}:'")

    def _release(self, estimate: float) -> None:
        self.usage.reserved = max(0.0, self.usage.reserved - estimate)

    def _call(self, fn: Callable[[], Any], what: str) -> Any:
        attempt = 0
        while True:
            self.limiter.wait()
            try:
                return fn()
            except Exception as exc:  # SDK raises ValueError("... status code N ...") on HTTP errors
                attempt += 1
                if attempt > self.settings.exa_max_retries or not _retryable(exc):
                    log.error("exa.call_failed", what=what, attempt=attempt, error=str(exc)[:300])
                    raise
                delay = min(60.0, 1.5 * 2 ** (attempt - 1))
                log.warning("exa.retry", what=what, attempt=attempt, delay_s=delay,
                            status=_status_from_error(exc), error=str(exc)[:160])
                self._sleep(delay)

    # ------------------------------------------------------------------ search
    def search(self, query: str, *, purpose: str, num_results: int = 8, include_domains: list[str] | None = None,
               exclude_domains: list[str] | None = None, text_chars: int | None = 6000,
               highlights: bool | dict = True, start_published_date: str | None = None,
               end_published_date: str | None = None, category: str | None = None,
               user_location: str | None = None, store_text: bool = True, search_type: str = "auto",
               ttl_hours: float | None = None) -> list[dict]:
        return self.search_meta(
            query, purpose=purpose, num_results=num_results, include_domains=include_domains,
            exclude_domains=exclude_domains, text_chars=text_chars, highlights=highlights,
            start_published_date=start_published_date, end_published_date=end_published_date,
            category=category, user_location=user_location, store_text=store_text,
            search_type=search_type, ttl_hours=ttl_hours,
        )["results"]

    def search_meta(self, query: str, *, purpose: str, num_results: int = 8,
                    include_domains: list[str] | None = None, exclude_domains: list[str] | None = None,
                    text_chars: int | None = 6000, highlights: bool | dict = True,
                    start_published_date: str | None = None, end_published_date: str | None = None,
                    category: str | None = None, user_location: str | None = None, store_text: bool = True,
                    search_type: str = "auto", ttl_hours: float | None = None) -> dict:
        self._validate_purpose(purpose)
        started = time.monotonic()
        contents: dict[str, Any] = {}
        if text_chars:
            contents["text"] = {"max_characters": text_chars}
        if highlights:
            contents["highlights"] = highlights
        req = {"query": query, "num_results": num_results, "include_domains": include_domains,
               "exclude_domains": exclude_domains, "contents": contents or None,
               "start_published_date": start_published_date, "type": search_type}
        for name, value in (("end_published_date", end_published_date), ("category", category),
                            ("user_location", user_location)):
            if value is not None:
                req[name] = value
        if not store_text:
            req["store_text"] = False
        key = "search:" + short_hash(json.dumps(req, sort_keys=True), 24)
        cached = None if not store_text and contents else self._cache_get(key, ttl_hours)
        if cached is not None:
            self._record("search", purpose, None, 0, cached=True, ref=key)
            return {"results": cached["results"], "request_id": cached.get("request_id"), "cost_usd": 0.0,
                    "latency_ms": round((time.monotonic() - started) * 1000), "cached": True}
        est = EST_SEARCH + (EST_CONTENTS_PER_URL * num_results if contents else 0)
        self._reserve(est, f"search[{purpose}]")
        try:
            kwargs = {k: v for k, v in req.items() if v is not None and k not in ("query", "store_text")}
            resp = self._call(lambda: self.client.search(query, **kwargs), f"search[{purpose}]")
        finally:
            self._release(est)
        results = [_norm_result(r) for r in getattr(resp, "results", []) or []]
        payload = {"results": results, "request_id": getattr(resp, "request_id", None)}
        stored_results = results if store_text else [
            {name: result[name] for name in ("url", "title", "published_date", "author", "score")}
            for result in results
        ]
        self._cache_put(key, "search", req, {"results": stored_results, "request_id": payload["request_id"]})
        cost = _cost_total(resp)
        self._record("search", purpose, cost, est, cached=False, ref=payload["request_id"] or key)
        log.info("exa.search", purpose=purpose, query=query[:120], results=len(results),
                 domains=include_domains, cost=cost)
        return {"results": results, "request_id": payload["request_id"],
                "cost_usd": cost if cost is not None else est,
                "latency_ms": round((time.monotonic() - started) * 1000), "cached": False}

    # ---------------------------------------------------------------- contents
    def contents(self, urls: list[str], *, purpose: str, text_chars: int = 40000,
                 ttl_hours: float | None = None) -> dict[str, dict]:
        """Fetch page text for URLs. Returns {canonical_url: result}; failed URLs are absent."""
        self._validate_purpose(purpose)
        out: dict[str, dict] = {}
        todo: list[str] = []
        for u in dict.fromkeys(canonical_url(u) for u in urls if u):
            hit = self._cache_get(f"contents:{short_hash(u, 24)}:{text_chars}", ttl_hours)
            if hit is not None:
                self._record("contents", purpose, None, 0, cached=True, ref=u)
                if hit.get("text"):
                    out[u] = hit
            else:
                todo.append(u)
        for i in range(0, len(todo), 10):
            batch = todo[i:i + 10]
            est = EST_CONTENTS_PER_URL * len(batch)
            self._reserve(est, f"contents[{purpose}]")
            try:
                resp = self._call(
                    lambda: self.client.get_contents(batch, text={"max_characters": text_chars}),
                    f"contents[{purpose}]",
                )
            except Exception as exc:
                log.warning("exa.contents_failed", purpose=purpose, urls=batch, error=str(exc)[:200])
                resp = None
            finally:
                self._release(est)
            got = {}
            for r in (getattr(resp, "results", None) or []) if resp is not None else []:
                nr = _norm_result(r)
                if nr["url"]:
                    got[canonical_url(nr["url"])] = nr
            for u in batch:
                nr = got.get(u) or {"url": u, "text": None, "error": "not returned"}
                self._cache_put(f"contents:{short_hash(u, 24)}:{text_chars}", "contents", {"url": u}, nr)
                if nr.get("text"):
                    out[u] = nr
            if resp is not None:
                self._record("contents", purpose, _cost_total(resp), est, cached=False,
                             ref=getattr(resp, "request_id", "") or "")
            log.info("exa.contents", purpose=purpose, requested=len(batch), returned=len(got))
        return out

    # ------------------------------------------------------------------- agent
    def agent_run(self, query: str, *, purpose: str, output_schema: dict, system_prompt: str | None = None,
                  ttl_hours: float | None = None, effort: str | None = None,
                  max_cost_usd: float | None = None) -> dict:
        """Run (or resume) an Exa Agent run with a strict output schema. Returns the run as JSON."""
        self._validate_purpose(purpose)
        effort = effort or self.settings.exa_agent_effort
        max_cost = max_cost_usd or self.settings.exa_agent_max_cost_usd
        req = {"query": query, "system_prompt": system_prompt, "output_schema": output_schema, "effort": effort}
        key = "agent:" + short_hash(json.dumps(req, sort_keys=True), 24)
        cached = self._cache_get(key, ttl_hours)
        if cached is not None:
            self._record("agent", purpose, None, 0, cached=True, ref=cached.get("id", ""))
            log.info("exa.agent_cache_hit", purpose=purpose, run_id=cached.get("id"))
            return cached

        with self._db_lock:
            row = self._db.execute("SELECT run_id, status FROM agent_runs WHERE key=?", (key,)).fetchone()
        run_id = row[0] if row and row[1] in ("queued", "running") else None

        self._reserve(max_cost, f"agent[{purpose}]")
        try:
            if run_id:
                log.info("exa.agent_resume", purpose=purpose, run_id=run_id)
            else:
                kwargs: dict[str, Any] = {"query": query, "output_schema": output_schema, "effort": effort,
                                          "metadata": {"purpose": purpose[:200], "app": "polgraph"}}
                if system_prompt:
                    kwargs["system_prompt"] = system_prompt
                if effort in ("auto", "ultra"):
                    kwargs["budget"] = {"maxCostDollars": max(1.0, max_cost)}
                run = self._call(lambda: self.client.agent.runs.create(**kwargs), f"agent.create[{purpose}]")
                run_id = run.id
                with self._db_lock, self._db:
                    self._db.execute("INSERT OR REPLACE INTO agent_runs VALUES (?,?,?,?,?,?,?)",
                                     (key, run_id, purpose, "running", utcnow(), None, None))
                log.info("exa.agent_created", purpose=purpose, run_id=run_id, effort=effort, max_cost=max_cost)
            run = self._call(
                lambda: self.client.agent.runs.poll_until_finished(
                    run_id, poll_interval=5000, timeout_ms=self.settings.exa_agent_timeout_s * 1000),
                f"agent.poll[{purpose}]",
            )
        finally:
            self._release(max_cost)
        data = _to_jsonable(run)
        status = data.get("status")
        cost = (data.get("cost_dollars") or data.get("costDollars") or {}).get("total")
        with self._db_lock, self._db:
            self._db.execute("UPDATE agent_runs SET status=?, completed_at=?, cost_usd=? WHERE key=?",
                             (status, utcnow(), cost, key))
        self._record("agent", purpose, float(cost) if cost is not None else None, max_cost, cached=False, ref=run_id)
        log.info("exa.agent_finished", purpose=purpose, run_id=run_id, status=status,
                 stop_reason=data.get("stop_reason") or data.get("stopReason"), cost=cost)
        if status != "completed":
            raise RuntimeError(f"Exa agent run {run_id} ended with status={status}: {data.get('error')}")
        self._cache_put(key, "agent", {"purpose": purpose, "query": query}, data)
        return data

    # -------------------------------------------------------------- reporting
    def usage_report(self) -> dict:
        with self._db_lock:
            rows = self._db.execute(
                "SELECT endpoint, COUNT(*), SUM(cached), SUM(cost_usd), SUM(estimated) FROM usage GROUP BY endpoint"
            ).fetchall()
            runs = self._db.execute(
                "SELECT run_id, purpose, status, cost_usd, created_at FROM agent_runs ORDER BY created_at"
            ).fetchall()
        return {
            "by_endpoint": [
                {"endpoint": r[0], "calls": r[1], "cache_hits": r[2] or 0, "cost_usd": round(r[3] or 0, 4),
                 "estimated_cost_rows": r[4] or 0}
                for r in rows
            ],
            "lifetime_cost_usd": round(self.lifetime_spend(), 4),
            "this_run": {"spent_usd": round(self.usage.spent, 4), "calls": self.usage.calls,
                         "cache_hits": self.usage.cache_hits},
            "agent_runs": [{"run_id": r[0], "purpose": r[1], "status": r[2], "cost_usd": r[3], "created_at": r[4]}
                           for r in runs],
            "budget": {"run_usd": self.settings.exa_run_budget_usd, "lifetime_usd": self.settings.exa_total_budget_usd},
        }


def grounding_urls(run: dict, field_prefix: str | None = None) -> dict[str, list[str]]:
    """Map output field -> cited URLs from an agent run's field-level grounding."""
    out: dict[str, list[str]] = {}
    for g in (run.get("output") or {}).get("grounding") or []:
        fld = g.get("field") or ""
        if field_prefix and not fld.startswith(field_prefix):
            continue
        urls = [c.get("url") for c in g.get("citations") or [] if c.get("url")]
        out.setdefault(fld, []).extend(urls)
    return out


def urls_for_item(run: dict, collection: str, index: int) -> list[str]:
    """Citation URLs whose grounding field refers to collection[index], e.g. `divisions[3].officeholder.full_name`
    or `divisions.3.name` (Exa reports field paths; both notations are accepted)."""
    pat = re.compile(rf"(^|\.){re.escape(collection)}(\[{index}\]|\.{index})(\.|\[|$)")
    urls: list[str] = []
    for fld, us in grounding_urls(run).items():
        if pat.search(fld):
            urls.extend(us)
    return list(dict.fromkeys(urls))


def urls_for_path(run: dict, parts: list[tuple[str, int]]) -> list[str]:
    """Citation URLs for a nested item, e.g. [("bodies", 2), ("offices", 1)] matches
    `bodies[2].offices[1].current_holders[0].full_name` or `bodies.2.offices.1.title_en`."""
    seg = r"(?:\.|\[|$).*?".join(rf"{re.escape(c)}(?:\[{i}\]|\.{i})" for c, i in parts)
    pat = re.compile(rf"(^|\.){seg}(\.|\[|$)")
    urls: list[str] = []
    for fld, us in grounding_urls(run).items():
        if pat.search(fld):
            urls.extend(us)
    return list(dict.fromkeys(urls))
