import json
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel

from system_d.config import Settings
from system_d.llm import (
    AnthropicLLM,
    LLMError,
    MeteredEmbedder,
    MeteredLLM,
    OpenAILLM,
    OpenAIEmbedder,
    Usage,
    UsageMeter,
    make_llm,
)


class Answer(BaseModel):
    value: int


def configured_settings(**overrides):
    values = {
        "anthropic_api_key": "anthropic-test-key",
        "openai_api_key": "openai-test-key",
        "llm_model": "claude-sonnet-5-5",
        "simulator_model": "claude-sonnet-5-5",
        "llm_max_retries": 2,
        "llm_backoff_base_s": 0.1,
        "llm_backoff_max_s": 2,
    }
    values.update(overrides)
    return replace(Settings(), **values)


def tool_response(value, *, usage=None):
    return {
        "model": "claude-sonnet-5-5",
        "content": [{"type": "tool_use", "name": "Answer", "input": value}],
        "usage": usage or {"input_tokens": 12, "output_tokens": 4},
    }


def openai_response(content, *, finish_reason="stop", usage=None):
    return {
        "model": "gpt-6.1-sol",
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 12, "completion_tokens": 4},
    }


def test_anthropic_forces_a_tool_and_parses_validated_input():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=tool_response({"value": 7}), request=request)

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    answer, usage = client.complete_json("system", "user", Answer, max_tokens=256)
    body = requests[0].read()
    request_body = json.loads(body)
    assert answer == Answer(value=7)
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert request_body["tools"][0]["input_schema"] == Answer.model_json_schema()
    assert request_body["tool_choice"] == {"type": "tool", "name": "Answer"}
    assert request_body["max_tokens"] == 256
    assert requests[0].headers["x-api-key"] == "anthropic-test-key"
    assert requests[0].headers["anthropic-version"] == "2023-06-01"


def test_anthropic_usage_prices_the_requested_model_not_a_dated_response_id():
    def handler(request):
        response = tool_response(
            {"value": 7},
            usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        response["model"] = "claude-sonnet-5-5-20261101"
        return httpx.Response(200, json=response, request=request)

    settings = configured_settings()
    client = AnthropicLLM(settings, "claude-sonnet-5-5", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    _, usage = client.complete_json("system", "user", Answer, max_tokens=256)
    assert usage.model == settings.llm_model
    assert usage.cost_usd(settings) == pytest.approx(12.0)


def test_anthropic_retries_validation_failure_then_succeeds():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        value = {"wrong": True} if calls == 1 else {"value": 5}
        return httpx.Response(
            200,
            json=tool_response(
                value,
                usage={"input_tokens": calls, "output_tokens": calls * 2},
            ),
            request=request,
        )

    delays = []
    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
    )
    answer, usage = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 5
    assert calls == 2
    assert delays
    assert (usage.input_tokens, usage.output_tokens) == (3, 6)


def test_metered_anthropic_records_usage_when_all_validation_attempts_fail():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=tool_response(
                {"wrong": True},
                usage={"input_tokens": 5, "output_tokens": 2},
            ),
            request=request,
        )

    settings = configured_settings()
    client = AnthropicLLM(
        settings,
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    meter = UsageMeter()
    metered = MeteredLLM(client, meter)

    with pytest.raises(LLMError, match="did not validate after 2 retries") as exc_info:
        metered.complete_json("system", "user", Answer, max_tokens=20)

    expected_usage = Usage("claude-sonnet-5-5", 15, 6)
    assert calls == 3
    assert exc_info.value.usage == expected_usage
    assert meter.usage.input_tokens == expected_usage.input_tokens
    assert meter.usage.output_tokens == expected_usage.output_tokens


def test_anthropic_retries_missing_tool_block_within_validation_budget():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        content = [{"type": "text", "text": "not a tool"}] if calls == 1 else tool_response({"value": 2})["content"]
        return httpx.Response(200, json={"content": content, "usage": {}}, request=request)

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
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

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
    )
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

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 1
    assert calls == 2


def test_anthropic_400_fails_immediately():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad request", request=request)

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    with pytest.raises(LLMError, match="^anthropic 400"):
        client.complete_json("system", "user", Answer, max_tokens=20)
    assert calls == 1


def test_anthropic_stops_after_max_retries():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="server error", request=request)

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    with pytest.raises(LLMError, match="after 2 retries"):
        client.complete_json("system", "user", Answer, max_tokens=20)
    assert calls == 3


def test_anthropic_chat_uses_simulator_model_and_returns_text_and_usage():
    request_data = {}

    def handler(request):
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
    client = AnthropicLLM(settings, "simulator-test", transport=httpx.MockTransport(handler), sleep=lambda _: None)
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

    client = AnthropicLLM(
        configured_settings(),
        "claude-sonnet-5-5",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=20)
    assert answer.value == 9
    assert calls == 2


def test_openai_json_schema_request_parses_and_validates_output():
    requests = []

    def handler(request):
        requests.append(request)
        response = openai_response('{"value": 7}')
        response["model"] = "gpt-6.1-sol-2026-01-01"
        return httpx.Response(200, json=response, request=request)

    client = OpenAILLM(
        configured_settings(),
        "gpt-6.1-sol",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    answer, usage = client.complete_json("system prompt", "user prompt", Answer, max_tokens=256)

    request = requests[0]
    payload = json.loads(request.read())
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer openai-test-key"
    assert payload == {
        "model": "gpt-6.1-sol",
        "max_completion_tokens": 256,
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "Answer",
                "schema": Answer.model_json_schema(),
                "strict": False,
            },
        },
    }
    assert answer == Answer(value=7)
    assert usage.model == "gpt-6.1-sol"
    assert (usage.input_tokens, usage.output_tokens) == (12, 4)


def test_openai_length_finish_retries_with_doubled_token_limit():
    requests = []
    responses = [
        openai_response(
            '{"value":',
            finish_reason="length",
            usage={"prompt_tokens": 5, "completion_tokens": 32},
        ),
        openai_response('{"value": 8}', usage={"prompt_tokens": 7, "completion_tokens": 14}),
    ]

    def handler(request):
        requests.append(json.loads(request.read()))
        return httpx.Response(200, json=responses[len(requests) - 1], request=request)

    client = OpenAILLM(
        configured_settings(),
        "gpt-6.1-sol",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    answer, usage = client.complete_json("system", "user", Answer, max_tokens=32)

    assert answer == Answer(value=8)
    assert [request["max_completion_tokens"] for request in requests] == [32, 64]
    assert (usage.input_tokens, usage.output_tokens) == (12, 46)


def test_openai_invalid_json_is_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        content = "{invalid" if calls == 1 else '{"value": 5}'
        return httpx.Response(
            200,
            json=openai_response(
                content,
                usage={"prompt_tokens": calls * 3, "completion_tokens": calls * 2},
            ),
            request=request,
        )

    client = OpenAILLM(
        configured_settings(),
        "gpt-6.1-sol",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    answer, usage = client.complete_json("system", "user", Answer, max_tokens=32)

    assert answer == Answer(value=5)
    assert calls == 2
    assert (usage.input_tokens, usage.output_tokens) == (9, 6)


def test_metered_openai_records_usage_when_all_validation_attempts_fail():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=openai_response(
                '{"wrong": true}',
                usage={"prompt_tokens": 4, "completion_tokens": 2},
            ),
            request=request,
        )

    settings = configured_settings(llm_model="gpt-6.1-sol")
    client = OpenAILLM(
        settings,
        "gpt-6.1-sol",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    meter = UsageMeter()
    metered = MeteredLLM(client, meter)

    with pytest.raises(LLMError, match="did not validate after 2 retries") as exc_info:
        metered.complete_json("system", "user", Answer, max_tokens=32)

    expected_usage = Usage("gpt-6.1-sol", 12, 6)
    assert calls == 3
    assert exc_info.value.usage == expected_usage
    assert meter.usage.input_tokens == expected_usage.input_tokens
    assert meter.usage.output_tokens == expected_usage.output_tokens
    assert meter.cost_usd(settings) == pytest.approx(expected_usage.cost_usd(settings))


def test_openai_honors_retry_after_then_succeeds():
    calls = 0
    delays = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, text="slow down", request=request)
        return httpx.Response(200, json=openai_response('{"value": 3}'), request=request)

    client = OpenAILLM(
        configured_settings(),
        "gpt-6.1-sol",
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
    )
    answer, _ = client.complete_json("system", "user", Answer, max_tokens=32)

    assert answer == Answer(value=3)
    assert calls == 2
    assert delays == [2.0]


def test_openai_400_fails_immediately():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad request", request=request)

    client = OpenAILLM(
        configured_settings(),
        "gpt-6.1-sol",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    with pytest.raises(LLMError, match="^openai 400"):
        client.complete_json("system", "user", Answer, max_tokens=32)
    assert calls == 1


def test_openai_chat_sends_system_first_and_returns_text_and_usage():
    request_data = {}

    def handler(request):
        request_data.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "model": "gpt-6-luna",
                "choices": [{"message": {"content": "Respondent reply"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6},
            },
            request=request,
        )

    client = OpenAILLM(
        configured_settings(),
        "gpt-6-luna",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    text, usage = client.chat(
        "persona and behavior",
        [{"role": "user", "content": "Question"}, {"role": "assistant", "content": "Prior reply"}],
        max_tokens=100,
    )

    assert request_data["model"] == "gpt-6-luna"
    assert request_data["messages"] == [
        {"role": "system", "content": "persona and behavior"},
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Prior reply"},
    ]
    assert request_data["max_completion_tokens"] == 100
    assert text == "Respondent reply"
    assert usage.model == "gpt-6-luna"
    assert (usage.input_tokens, usage.output_tokens) == (10, 6)


def test_make_llm_routes_structured_calls_and_chat_to_their_models(monkeypatch):
    from system_d import llm as llm_module

    clients = {}

    class RecordingClient:
        def __init__(self, model):
            self.model = model
            self.complete_calls = 0
            self.chat_calls = 0

        def complete_json(self, system, user, output_model, max_tokens):
            self.complete_calls += 1
            return output_model(value=1), Usage(self.model, 1, 1)

        def chat(self, system, messages, max_tokens):
            self.chat_calls += 1
            return self.model, Usage(self.model, 1, 1)

    def make_client(settings, model):
        clients[model] = RecordingClient(model)
        return clients[model]

    monkeypatch.setattr(llm_module, "_make_model_client", make_client)
    router = make_llm(configured_settings(llm_model="gpt-6.1-sol", simulator_model="gpt-6-luna"))
    router.complete_json("system", "user", Answer, max_tokens=32)
    assert router.chat("simulator", [], max_tokens=32)[0] == "gpt-6-luna"

    assert clients["gpt-6.1-sol"].complete_calls == 1
    assert clients["gpt-6.1-sol"].chat_calls == 0
    assert clients["gpt-6-luna"].complete_calls == 0
    assert clients["gpt-6-luna"].chat_calls == 1


def test_make_llm_with_openai_models_does_not_require_anthropic_key():
    settings = Settings.from_env({"OPENAI_API_KEY": "openai-test-key"})
    router = make_llm(settings)
    assert router.complete_client.model == "gpt-6.1-sol"
    assert router.chat_client.model == "gpt-6-luna"


def test_gpt_6_luna_usage_uses_its_configured_price():
    settings = Settings.from_env({})
    usage = Usage("gpt-6-luna", 1_000_000, 1_000_000)
    assert usage.cost_usd(settings) == pytest.approx(0.6)


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


def test_metered_llm_and_embedder_forward_calls_and_accumulate_usage():
    expected_llm_usage = Usage("claude-sonnet-5-5", 10, 3)
    expected_embed_usage = Usage("text-embedding-3-small", 7, 0)

    class FakeLLM:
        def complete_json(self, system, user, output_model, max_tokens):
            return output_model(value=4), expected_llm_usage

        def chat(self, system, messages, max_tokens):
            return "reply", expected_llm_usage

    class FakeEmbedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts], expected_embed_usage

    meter = UsageMeter()
    llm = MeteredLLM(FakeLLM(), meter)
    embedder = MeteredEmbedder(FakeEmbedder(), meter)
    result, complete_usage = llm.complete_json("system", "user", Answer, 20)
    reply, chat_usage = llm.chat("system", [{"role": "user", "content": "hello"}], 20)
    vectors, embed_usage = embedder.embed(["one", "two"])

    assert result == Answer(value=4)
    assert reply == "reply"
    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    assert complete_usage == expected_llm_usage
    assert chat_usage == expected_llm_usage
    assert embed_usage == expected_embed_usage
    assert meter.usage.input_tokens == 27
    assert meter.usage.output_tokens == 6
