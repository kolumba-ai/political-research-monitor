from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel

from system_d.config import Settings
from system_d.llm import AnthropicLLM, LLMError, OpenAIEmbedder, Usage, UsageMeter


class Answer(BaseModel):
    value: int


def configured_settings(**overrides):
    return replace(
        Settings(),
        anthropic_api_key="anthropic-test-key",
        openai_api_key="openai-test-key",
        llm_max_retries=2,
        llm_backoff_base_s=0.1,
        llm_backoff_max_s=2,
        **overrides,
    )


def tool_response(value, *, usage=None):
    return {
        "model": "claude-sonnet-5-5",
        "content": [{"type": "tool_use", "name": "Answer", "input": value}],
        "usage": usage or {"input_tokens": 12, "output_tokens": 4},
    }


def test_anthropic_forces_a_tool_and_parses_validated_input():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=tool_response({"value": 7}), request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    answer, usage = client.complete_json("system", "user", Answer, max_tokens=256)
    body = requests[0].read()
    import json

    request_body = json.loads(body)
    assert answer == Answer(value=7)
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert request_body["tools"][0]["input_schema"] == Answer.model_json_schema()
    assert request_body["tool_choice"] == {"type": "tool", "name": "Answer"}
    assert request_body["max_tokens"] == 256
    assert requests[0].headers["x-api-key"] == "anthropic-test-key"
    assert requests[0].headers["anthropic-version"] == "2023-06-01"


def test_anthropic_retries_validation_failure_then_succeeds():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        value = {"wrong": True} if calls == 1 else {"value": 5}
        return httpx.Response(200, json=tool_response(value), request=request)

    delays = []
    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=delays.append)
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 5
    assert calls == 2
    assert delays


def test_anthropic_retries_missing_tool_block_within_validation_budget():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        content = [{"type": "text", "text": "not a tool"}] if calls == 1 else tool_response({"value": 2})["content"]
        return httpx.Response(200, json={"content": content, "usage": {}}, request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 2
    assert calls == 2


def test_anthropic_honors_retry_after_then_succeeds():
    calls = 0
    delays = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, text="slow down", request=request)
        return httpx.Response(200, json=tool_response({"value": 3}), request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=delays.append)
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 3
    assert calls == 2
    assert delays == [2.0]


def test_anthropic_retries_529():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(529, text="overloaded", request=request)
        return httpx.Response(200, json=tool_response({"value": 1}), request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 1
    assert calls == 2


def test_anthropic_400_fails_immediately():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad request", request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(LLMError, match="^anthropic 400"):
        client.complete_json("system", "user", Answer, max_tokens=20)
    assert calls == 1


def test_anthropic_stops_after_max_retries():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="server error", request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(LLMError, match="after 2 retries"):
        client.complete_json("system", "user", Answer, max_tokens=20)
    assert calls == 3


def test_anthropic_chat_uses_simulator_model_and_returns_text_and_usage():
    request_data = {}

    def handler(request):
        import json

        request_data.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Primera respuesta."}, {"type": "text", "text": "Segunda."}],
                "usage": {"input_tokens": 9, "output_tokens": 3},
            },
            request=request,
        )

    settings = configured_settings(simulator_model="simulator-test")
    client = AnthropicLLM(settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    text, usage = client.chat("persona", [{"role": "user", "content": "Hola"}], max_tokens=100)
    assert request_data["model"] == "simulator-test"
    assert request_data["messages"] == [{"role": "user", "content": "Hola"}]
    assert text == "Primera respuesta.\nSegunda."
    assert usage.input_tokens == 9
    assert usage.output_tokens == 3


def test_anthropic_retries_transport_timeout():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=tool_response({"value": 9}), request=request)

    client = AnthropicLLM(configured_settings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 9
    assert calls == 2


def test_openai_embedding_auth_usage_and_dimension_validation():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [{"embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 6, "total_tokens": 6},
            },
            request=request,
        )

    settings = configured_settings(embedding_dim=2)
    embedder = OpenAIEmbedder(settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    vectors, usage = embedder.embed(["claim"])
    assert vectors == [[0.1, 0.2]]
    assert usage.input_tokens == 6
    assert usage.output_tokens == 0
    assert requests[0].headers["Authorization"] == "Bearer openai-test-key"
    assert embedder.embed([]) == ([], Usage(settings.embedding_model, 0, 0))

    wrong_dim = OpenAIEmbedder(
        configured_settings(embedding_dim=3),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    with pytest.raises(ValueError, match="embedding dimension"):
        wrong_dim.embed(["claim"])


def test_usage_addition_cost_meter_and_unknown_model_warning(caplog):
    settings = Settings()
    usage = Usage("claude-sonnet-5-5", 1_000_000, 1_000_000)
    assert usage.cost_usd(settings) == pytest.approx(12)
    meter = UsageMeter()
    meter.add(Usage("claude-sonnet-5-5", 1_000_000, 0))
    meter.add(Usage("claude-haiku-5-5", 0, 2_000_000))
    assert meter.cost_usd(settings) == pytest.approx(3)
    assert (Usage("claude-sonnet-5-5", 1, 2) + Usage("claude-sonnet-5-5", 3, 4)).input_tokens == 4
    unknown = Usage("unknown-test-model", 10, 10)
    assert unknown.cost_usd(settings) == 0
    assert unknown.cost_usd(settings) == 0
    assert sum("No token pricing configured" in record.message for record in caplog.records) == 1
