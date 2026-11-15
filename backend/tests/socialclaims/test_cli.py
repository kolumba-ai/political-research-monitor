import pytest

from socialclaims import claims as claims_module
from socialclaims import cli
from socialclaims.config import SocialSettings


def test_extract_claims_max_tokens_is_validated_and_passed(monkeypatch):
    settings = SocialSettings(_env_file=None, database_url="postgresql://unused",
                              anthropic_api_key="test-key", claim_model="fake-model")
    llm_max_tokens = []

    class FakeLLM:
        def __init__(self, api_key, model, base_url, max_tokens=8000):
            llm_max_tokens.append(max_tokens)

    class FakeExtractor:
        def __init__(self, conn, graph, llm, country):
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
