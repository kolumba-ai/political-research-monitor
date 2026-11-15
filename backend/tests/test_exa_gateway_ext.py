import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest

from polgraph.exa_gateway import BudgetExceeded, ExaGateway, PgRateLimiter
from polgraph.ids import short_hash

from .fakes import FakeExa


class RichFakeExa(FakeExa):
    def __init__(self):
        super().__init__()
        self.search_kwargs = []

    def search(self, query, **kwargs):
        self.search_kwargs.append(kwargs)
        self.calls.append(("search", query))
        return SimpleNamespace(
            results=[SimpleNamespace(url="https://example.mx/article", title="Article", published_date="2026-01-01",
                                     author="Reporter", score=0.87, text="private page text",
                                     highlights=["private highlight"])],
            cost_dollars=SimpleNamespace(total=0.007),
            request_id="req-test",
        )


def test_search_forwards_new_parameters_and_search_meta(settings):
    client = RichFakeExa()
    gateway = ExaGateway(settings, client=client, sleep=lambda _: None)

    result = gateway.search_meta(
        "claim", purpose="ev:claim", num_results=3, include_domains=["gob.mx"],
        exclude_domains=["x.com"], text_chars=500, highlights={"query": "biased query"},
        start_published_date="2026-01-01", end_published_date="2026-02-01",
        category="news", user_location="MX",
    )

    request = client.search_kwargs[0]
    assert request["end_published_date"] == "2026-02-01"
    assert request["category"] == "news"
    assert request["user_location"] == "MX"
    assert request["contents"]["highlights"] == {"query": "biased query"}
    assert result["request_id"] == "req-test"
    assert result["cost_usd"] == pytest.approx(0.007)
    assert result["latency_ms"] >= 0
    assert result["cached"] is False
    assert result["results"][0]["score"] == pytest.approx(0.87)
    cached = gateway.search_meta("claim", purpose="ev:claim", num_results=3, include_domains=["gob.mx"],
                                 exclude_domains=["x.com"], text_chars=500, highlights={"query": "biased query"},
                                 start_published_date="2026-01-01", end_published_date="2026-02-01",
                                 category="news", user_location="MX")
    assert cached["cached"] is True
    assert cached["cost_usd"] == 0.0


def test_plain_search_cache_key_is_unchanged(settings):
    gateway = ExaGateway(settings, client=RichFakeExa(), sleep=lambda _: None)
    query = "legacy cache key"
    old_request = {
        "query": query,
        "num_results": 8,
        "include_domains": None,
        "exclude_domains": None,
        "contents": {"text": {"max_characters": 6000}, "highlights": True},
        "start_published_date": None,
        "type": "auto",
    }
    old_key = "search:" + short_hash(json.dumps(old_request, sort_keys=True), 24)

    gateway.search(query, purpose="system-a")

    assert gateway._db.execute("SELECT key FROM cache").fetchone()[0] == old_key


def test_store_text_false_returns_content_but_caches_only_metadata(settings):
    gateway = ExaGateway(settings, client=RichFakeExa(), sleep=lambda _: None)
    query = "text-free cache"
    response = gateway.search_meta(query, purpose="ev:claim", store_text=False)
    req = {
        "query": query,
        "num_results": 8,
        "include_domains": None,
        "exclude_domains": None,
        "contents": {"text": {"max_characters": 6000}, "highlights": True},
        "start_published_date": None,
        "type": "auto",
        "store_text": False,
    }
    key = "search:" + short_hash(json.dumps(req, sort_keys=True), 24)
    cached = gateway._cache_get(key, None)

    assert response["results"][0]["text"] == "private page text"
    assert response["results"][0]["highlights"] == ["private highlight"]
    assert "text" not in cached["results"][0]
    assert "highlights" not in cached["results"][0]
    assert cached["results"][0]["score"] == pytest.approx(0.87)
    assert key != "search:" + short_hash(json.dumps({k: v for k, v in req.items() if k != "store_text"},
                                                   sort_keys=True), 24)


def test_scoped_monthly_budget_counts_only_matching_current_month_usage(settings):
    settings.exa_run_budget_usd = 20
    gateway = ExaGateway(settings, client=RichFakeExa(), sleep=lambda _: None,
                         budget_scope="ev", monthly_budget_usd=0.019)
    now = datetime.now(timezone.utc)
    previous_month = 12 if now.month == 1 else now.month - 1
    previous_year = now.year - 1 if now.month == 1 else now.year
    last_month = datetime(previous_year, previous_month, 15, tzinfo=timezone.utc).isoformat()
    this_month = datetime(now.year, now.month, 2, tzinfo=timezone.utc).isoformat()
    gateway._db.executemany(
        "INSERT INTO usage(ts, endpoint, purpose, cost_usd, estimated, cached, ref) VALUES (?,?,?,?,?,?,?)",
        [
            (last_month, "search", "ev:old", 1.0, 0, 0, ""),
            (this_month, "search", "system-a:other", 1.0, 0, 0, ""),
            (this_month, "search", "ev:claim", 0.003, 0, 0, ""),
        ],
    )
    gateway._db.commit()

    result = gateway.search_meta("budget test", purpose="ev:claim", num_results=1, text_chars=None, highlights=False)

    assert result["request_id"] == "req-test"
    with pytest.raises(BudgetExceeded):
        ExaGateway(settings, client=RichFakeExa(), sleep=lambda _: None, budget_scope="ev",
                   monthly_budget_usd=0.01).search("over budget", purpose="ev:claim",
                                                   num_results=1, text_chars=None, highlights=False)
    with pytest.raises(ValueError, match="purpose must start with 'ev:'"):
        gateway.search("wrong purpose", purpose="system-a", text_chars=None, highlights=False)


def test_pg_rate_limiters_share_slots_on_socialclaims_test():
    database_url = "postgresql://ubuntu:ubuntu@localhost:5432/socialclaims_test"
    raw_key = f"test-{os.getpid()}-{datetime.now(timezone.utc).timestamp()}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    now = [100.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    first = PgRateLimiter(database_url, raw_key, 2, clock=lambda: now[0], sleep=sleep)
    second = PgRateLimiter(database_url, raw_key, 2, clock=lambda: now[0], sleep=sleep)
    try:
        first.wait()
        second.wait()
        assert sleeps == pytest.approx([0.5])
        assert first.key == key_hash
        with psycopg.connect(database_url) as conn:
            next_at = conn.execute("SELECT next_at FROM exa_rate_limit WHERE key = %s", (key_hash,)).fetchone()[0]
        assert next_at == pytest.approx(101.0)
    except psycopg.OperationalError as exc:
        pytest.skip(f"socialclaims_test database unavailable: {exc}")
    finally:
        try:
            with psycopg.connect(database_url) as conn:
                conn.execute("DELETE FROM exa_rate_limit WHERE key = %s", (key_hash,))
        except psycopg.OperationalError:
            pass


def test_exa_honors_capped_retry_after(settings):
    sleeps = []

    class RetryableError(ValueError):
        response = SimpleNamespace(headers={"Retry-After": "180"})

        def __init__(self):
            super().__init__("Request failed with status code 503")

    class Client:
        attempts = 0

        def search(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise RetryableError()
            return SimpleNamespace(results=[], cost_dollars=SimpleNamespace(total=0.001), request_id="retry-ok")

    class NoWait:
        def wait(self):
            return None

    gateway = ExaGateway(settings, client=Client(), sleep=sleeps.append, limiter=NoWait())

    result = gateway.search("retry after", purpose="test", text_chars=None, highlights=False)

    assert result == []
    assert sleeps == [120.0]
