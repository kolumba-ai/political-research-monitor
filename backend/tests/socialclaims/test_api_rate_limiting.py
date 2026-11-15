import hashlib

import httpx
import pytest

from polgraph.exa_gateway import PgRateLimiter, retry_after_seconds
from socialclaims.claims import AnthropicLLM
from socialclaims.config import SocialSettings
from socialclaims.oriane import OrianeClient
from socialclaims.pastdev import PastDevClient

RATE_LIMITER_URL = "postgresql://rate-limit.test/socialclaims"


def test_social_rate_limit_defaults_are_off():
    settings = SocialSettings(_env_file=None)

    assert settings.rate_limiter_database_url is None
    assert settings.anthropic_requests_per_second == 2.0
    assert settings.oriane_requests_per_second == 1.0
    assert settings.pastdev_requests_per_second == 2.0


def test_retry_after_accepts_seconds_and_caps_at_120():
    assert retry_after_seconds({"Retry-After": "12.5"}) == 12.5
    assert retry_after_seconds({"Retry-After": "180"}) == 120.0
    assert retry_after_seconds({"Retry-After": "not-seconds"}) is None


def _anthropic_response():
    return httpx.Response(
        200,
        json={"content": [{"type": "text", "text": '{"claims": []}'}], "stop_reason": "end_turn", "usage": {}},
    )


@pytest.mark.parametrize(
    ("client_type", "prefix", "rps", "call"),
    [
        ("anthropic", "anthropic", 2.0, lambda client: client.extract("sys", "prompt", {
            "name": "record_claims", "input_schema": {},
        })),
        ("oriane", "oriane", 1.0, lambda client: client.search_contents({}, limit=1)),
        ("pastdev", "pastdev", 2.0, lambda client: client.get("/audiences")),
    ],
)
@pytest.mark.parametrize("enabled", [False, True])
def test_api_clients_apply_optional_shared_limiter(monkeypatch, client_type, prefix, rps, call, enabled):
    waits = []
    monkeypatch.setattr(PgRateLimiter, "wait", lambda limiter: waits.append(limiter.key))

    def handler(request):
        if client_type == "anthropic":
            return _anthropic_response()
        if client_type == "oriane":
            return httpx.Response(200, json={"metadata": {}, "data": {"results": []}})
        return httpx.Response(200, json={"audiences": []})

    transport = httpx.MockTransport(handler)
    options = {"rate_limiter_database_url": RATE_LIMITER_URL} if enabled else {}
    if client_type == "anthropic":
        client = AnthropicLLM("test-api-key", "model", transport=transport, sleep=lambda _: None, **options)
    elif client_type == "oriane":
        client = OrianeClient("test-api-key", "https://test", transport=transport, sleep=lambda _: None, **options)
    else:
        client = PastDevClient("test-api-key", transport=transport, sleep=lambda _: None, **options)

    call(client)

    if enabled:
        expected_key = hashlib.sha256(f"{prefix}:test-api-key".encode()).hexdigest()[:16]
        assert client.limiter.key == expected_key
        assert "test-api-key" not in client.limiter.key
        assert client.limiter.database_url == RATE_LIMITER_URL
        assert waits == [expected_key]
        assert client.limiter.min_interval == pytest.approx(1 / rps)
    else:
        assert client.limiter is None
        assert waits == []


def test_anthropic_honors_capped_retry_after():
    responses = iter([
        httpx.Response(503, headers={"Retry-After": "180"}),
        _anthropic_response(),
    ])
    sleeps = []
    client = AnthropicLLM(
        "key", "model", transport=httpx.MockTransport(lambda _: next(responses)), sleep=sleeps.append,
    )

    client.extract("sys", "prompt", {"name": "record_claims", "input_schema": {}})

    assert sleeps == [120.0]


def test_oriane_honors_numeric_retry_after():
    responses = iter([
        httpx.Response(429, json={"error": {"message": "busy"}}, headers={"Retry-After": "1.25"}),
        httpx.Response(200, json={"metadata": {}, "data": {"results": []}}),
    ])
    sleeps = []
    client = OrianeClient(
        "key", "https://test", max_retries=1, transport=httpx.MockTransport(lambda _: next(responses)),
        sleep=sleeps.append,
    )

    client.search_contents({}, limit=1)

    assert sleeps == [1.25]


def test_pastdev_honors_numeric_retry_after():
    responses = iter([
        httpx.Response(503, text="busy", headers={"Retry-After": "0.5"}),
        httpx.Response(200, json={"audiences": []}),
    ])
    sleeps = []
    client = PastDevClient(
        "key", max_retries=1, transport=httpx.MockTransport(lambda _: next(responses)), sleep=sleeps.append,
    )

    client.get("/audiences")

    assert sleeps == [0.5]
