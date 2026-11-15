import pytest

from socialclaims import claims as claims_module
from socialclaims import cli
from socialclaims.config import SocialSettings


def test_extract_claims_max_tokens_is_validated_and_passed(monkeypatch):
    settings = SocialSettings(_env_file=None, database_url="postgresql://unused",
                              anthropic_api_key="test-key", claim_model="fake-model",
                              rate_limiter_database_url="postgresql://limiter",
                              anthropic_requests_per_second=1.25)
    llm_max_tokens = []
    llm_limiter_options = []

    class FakeLLM:
        def __init__(self, api_key, model, base_url, max_tokens=8000, **limiter_options):
            llm_max_tokens.append(max_tokens)
            llm_limiter_options.append(limiter_options)

    class FakeExtractor:
        def __init__(self, conn, graph, llm, country, skip_any_version=False):
            pass

        def run(self, politician_id, max_posts, shard):
            return {"politician_id": politician_id}

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "connect", lambda database_url: object())
    monkeypatch.setattr(cli, "_graph", lambda settings: object())
    monkeypatch.setattr(claims_module, "AnthropicLLM", FakeLLM)
    monkeypatch.setattr(claims_module, "Extractor", FakeExtractor)

    with pytest.raises(SystemExit):
        cli.main(["extract-claims", "person:MX:test", "--max-tokens", "0"])
    assert cli.main(["extract-claims", "person:MX:test", "--confirm-spend", "--max-tokens", "16000"]) == 0
    assert llm_max_tokens == [16000]
    assert llm_limiter_options == [{
        "rate_limiter_database_url": "postgresql://limiter",
        "requests_per_second": 1.25,
    }]


def test_openai_model_without_api_key_returns_two(monkeypatch, capsys):
    settings = SocialSettings(
        _env_file=None,
        database_url="postgresql://unused",
        claim_model="gpt-6.1-sol",
        openai_api_key=None,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "connect", lambda database_url: object())
    monkeypatch.setattr(cli, "_graph", lambda settings: object())

    result = cli.main(["extract-claims", "person:MX:test", "--confirm-spend"])

    assert result == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_unrecognized_o_model_prefix_uses_anthropic_key(monkeypatch, capsys):
    settings = SocialSettings(
        _env_file=None,
        database_url="postgresql://unused",
        claim_model="other-model",
        openai_api_key=None,
        anthropic_api_key=None,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "connect", lambda database_url: object())
    monkeypatch.setattr(cli, "_graph", lambda settings: object())

    result = cli.main(["extract-claims", "person:MX:test", "--confirm-spend"])

    assert result == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_oriane_client_receives_shared_limiter_settings():
    settings = SocialSettings(
        _env_file=None,
        oriane_api_key="oriane-key",
        rate_limiter_database_url="postgresql://limiter",
        oriane_requests_per_second=0.5,
    )

    client = cli._client(settings)

    assert client.limiter.database_url == "postgresql://limiter"
    assert client.limiter.key != "oriane:oriane-key"
    assert client.limiter.min_interval == pytest.approx(2.0)
    client._http.close()
