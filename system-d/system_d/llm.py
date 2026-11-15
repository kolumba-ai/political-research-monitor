"""HTTP clients for Anthropic JSON/chat and OpenAI embeddings."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings, provider_for

logger = logging.getLogger(__name__)
_warned_unknown_models: set[str] = set()
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """An LLM request failed or returned invalid structured output."""

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    _per_model: dict[str, tuple[int, int]] = field(default_factory=dict, repr=False, compare=False)

    def __add__(self, other: Usage) -> Usage:
        if not isinstance(other, Usage):
            return NotImplemented
        totals: dict[str, tuple[int, int]] = {}
        for usage in (self, other):
            parts = usage._per_model or {usage.model: (usage.input_tokens, usage.output_tokens)}
            for model, (input_tokens, output_tokens) in parts.items():
                old_input, old_output = totals.get(model, (0, 0))
                totals[model] = (old_input + input_tokens, old_output + output_tokens)
        model = next(iter(totals)) if len(totals) == 1 else "mixed"
        return Usage(
            model=model,
            input_tokens=sum(item[0] for item in totals.values()),
            output_tokens=sum(item[1] for item in totals.values()),
            _per_model=totals,
        )

    def __radd__(self, other: Any) -> Usage:
        if other == 0:
            return self
        if isinstance(other, Usage):
            return other + self
        return NotImplemented

    def cost_usd(self, settings: Settings) -> float:
        parts = self._per_model or {self.model: (self.input_tokens, self.output_tokens)}
        total = 0.0
        for model, (input_tokens, output_tokens) in parts.items():
            if input_tokens == 0 and output_tokens == 0:
                continue
            prices = settings.prices_usd_per_million.get(model)
            if prices is None:
                if model not in _warned_unknown_models:
                    logger.warning("No token pricing configured for model %s; estimating cost as zero", model)
                    _warned_unknown_models.add(model)
                continue
            total += (input_tokens * prices.get("input", 0.0) + output_tokens * prices.get("output", 0.0)) / 1_000_000
        return total


class UsageMeter:
    def __init__(self) -> None:
        self._usage = Usage(model="mixed", input_tokens=0, output_tokens=0, _per_model={})

    def add(self, usage: Usage) -> None:
        self._usage = self._usage + usage

    @property
    def usage(self) -> Usage:
        return self._usage

    def cost_usd(self, settings: Settings) -> float:
        return self._usage.cost_usd(settings)


class LLM(Protocol):
    def complete_json(self, system: str, user: str, output_model: type[T], max_tokens: int) -> tuple[T, Usage]: ...

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, Usage]: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]: ...


class MeteredLLM:
    def __init__(self, llm: LLM, meter: UsageMeter) -> None:
        self.llm = llm
        self.meter = meter

    def complete_json(self, system: str, user: str, output_model: type[T], max_tokens: int) -> tuple[T, Usage]:
        try:
            result, usage = self.llm.complete_json(
                system=system,
                user=user,
                output_model=output_model,
                max_tokens=max_tokens,
            )
        except LLMError as exc:
            if exc.usage is not None:
                self.meter.add(exc.usage)
            raise
        self.meter.add(usage)
        return result, usage

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, Usage]:
        result, usage = self.llm.chat(system=system, messages=messages, max_tokens=max_tokens)
        self.meter.add(usage)
        return result, usage


class MeteredEmbedder:
    def __init__(self, embedder: Embedder, meter: UsageMeter) -> None:
        self.embedder = embedder
        self.meter = meter

    def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        result, usage = self.embedder.embed(texts)
        self.meter.add(usage)
        return result, usage


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class _HTTPClient:
    def __init__(self, settings: Settings, sleep: Callable[[float], None]) -> None:
        self.settings = settings
        self.sleep = sleep

    def _delay(self, retry_index: int, retry_after: str | None = None) -> float:
        requested = _retry_after_seconds(retry_after)
        if requested is not None:
            return min(requested, self.settings.llm_backoff_max_s)
        exponential = self.settings.llm_backoff_base_s * (2**retry_index)
        return min(exponential * random.uniform(0.5, 1.5), self.settings.llm_backoff_max_s)

    def _post(self, client: httpx.Client, url: str, payload: dict, service: str) -> httpx.Response:
        retries = self.settings.llm_max_retries
        retry_statuses = {429, 529}
        for retry_index in range(retries + 1):
            try:
                response = client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if retry_index >= retries:
                    raise LLMError(f"{service} request failed after {retries} retries: {exc}") from exc
                self.sleep(self._delay(retry_index))
                continue
            if response.status_code in retry_statuses or response.status_code >= 500:
                if retry_index >= retries:
                    raise LLMError(f"{service} {response.status_code} after {retries} retries: {response.text}")
                self.sleep(self._delay(retry_index, response.headers.get("retry-after")))
                continue
            if response.status_code >= 400:
                raise LLMError(f"{service} {response.status_code}: {response.text}")
            return response
        raise LLMError(f"{service} request failed after {retries} retries")

    @staticmethod
    def _usage(data: dict[str, Any], model: str, input_key: str, output_key: str) -> Usage:
        usage = data.get("usage") or {}
        return Usage(
            model=model,
            input_tokens=int(usage.get(input_key) or 0),
            output_tokens=int(usage.get(output_key) or 0),
        )


class AnthropicLLM(_HTTPClient):
    def __init__(
        self,
        settings: Settings,
        model: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(settings, sleep)
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        self.model = model
        self.client = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
            transport=transport,
            timeout=settings.llm_timeout_s,
        )

    def complete_json(self, system: str, user: str, output_model: type[T], max_tokens: int) -> tuple[T, Usage]:
        tool_name = output_model.__name__
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [
                {
                    "name": tool_name,
                    "description": output_model.__doc__ or "Return structured output matching this schema.",
                    "input_schema": output_model.model_json_schema(),
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        last_error: Exception | None = None
        total_usage = Usage(self.model, 0, 0)
        for retry_index in range(self.settings.llm_max_retries + 1):
            response = self._post(self.client, "/v1/messages", payload, "anthropic")
            data = response.json()
            total_usage += self._usage(data, self.model, "input_tokens", "output_tokens")
            tool_block = next(
                (
                    block
                    for block in data.get("content", [])
                    if block.get("type") == "tool_use" and block.get("name") == tool_name
                ),
                None,
            )
            if tool_block is None:
                last_error = ValueError("response missing the requested tool block")
            else:
                try:
                    return output_model.model_validate(tool_block.get("input")), total_usage
                except ValidationError as exc:
                    last_error = exc
            if retry_index < self.settings.llm_max_retries:
                self.sleep(self._delay(retry_index))
        raise LLMError(
            f"anthropic response did not validate after {self.settings.llm_max_retries} retries",
            usage=total_usage,
        ) from last_error

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, Usage]:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        response = self._post(self.client, "/v1/messages", payload, "anthropic")
        data = response.json()
        text = "\n".join(
            block["text"] for block in data.get("content", []) if block.get("type") == "text" and "text" in block
        )
        return text, self._usage(data, self.model, "input_tokens", "output_tokens")


class OpenAILLM(_HTTPClient):
    def __init__(
        self,
        settings: Settings,
        model: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(settings, sleep)
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")
        self.model = model
        self.client = httpx.Client(
            base_url=settings.openai_base_url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            transport=transport,
            timeout=settings.llm_timeout_s,
        )

    def complete_json(self, system: str, user: str, output_model: type[T], max_tokens: int) -> tuple[T, Usage]:
        payload = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "schema": output_model.model_json_schema(),
                    "strict": False,
                },
            },
        }
        current_max_tokens = max_tokens
        last_error: Exception | None = None
        total_usage = Usage(self.model, 0, 0)
        for retry_index in range(self.settings.llm_max_retries + 1):
            payload["max_completion_tokens"] = current_max_tokens
            response = self._post(self.client, "/v1/chat/completions", payload, "openai")
            data = response.json()
            total_usage += self._usage(data, self.model, "prompt_tokens", "completion_tokens")
            choices = data.get("choices") or []
            choice = choices[0] if choices else {}
            if choice.get("finish_reason") == "length":
                last_error = ValueError("response was truncated at the token limit")
                current_max_tokens = min(current_max_tokens * 2, 16_000)
            else:
                message = choice.get("message") or {}
                content = message.get("content")
                try:
                    if not isinstance(content, str):
                        raise TypeError("response did not include JSON content")
                    result = output_model.model_validate(json.loads(content))
                    return result, total_usage
                except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                    last_error = exc
            if retry_index < self.settings.llm_max_retries:
                self.sleep(self._delay(retry_index))
        raise LLMError(
            f"openai response did not validate after {self.settings.llm_max_retries} retries",
            usage=total_usage,
        ) from last_error

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, Usage]:
        payload = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if self.settings.simulator_reasoning_effort:
            payload["reasoning_effort"] = self.settings.simulator_reasoning_effort
        current_max_tokens = max_tokens
        total_usage = Usage(self.model, 0, 0)
        last_error: Exception | None = None
        for retry_index in range(self.settings.llm_max_retries + 1):
            payload["max_completion_tokens"] = current_max_tokens
            response = self._post(self.client, "/v1/chat/completions", payload, "openai")
            data = response.json()
            total_usage += self._usage(data, self.model, "prompt_tokens", "completion_tokens")
            choices = data.get("choices") or []
            choice = choices[0] if choices else {}
            message = choice.get("message") or {}
            content = message.get("content")
            if choice.get("finish_reason") == "length":
                last_error = ValueError("response was truncated at the token limit")
            elif not isinstance(content, str) or not content.strip():
                last_error = ValueError("response did not include message content")
            else:
                return content, total_usage
            current_max_tokens = min(current_max_tokens * 2, 4_000)
            if retry_index < self.settings.llm_max_retries:
                self.sleep(self._delay(retry_index))
        raise LLMError(
            f"openai chat did not return a complete response after {self.settings.llm_max_retries} retries",
            usage=total_usage,
        ) from last_error


class OpenAIEmbedder(_HTTPClient):
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(settings, sleep)
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")
        self.client = httpx.Client(
            base_url=settings.openai_base_url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            transport=transport,
            timeout=settings.llm_timeout_s,
        )

    def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        if not texts:
            return [], Usage(self.settings.embedding_model, 0, 0)
        response = self._post(
            self.client,
            "/v1/embeddings",
            {"model": self.settings.embedding_model, "input": texts},
            "openai",
        )
        data = response.json()
        vectors = [item["embedding"] for item in data.get("data", [])]
        if len(vectors) != len(texts):
            raise LLMError(f"openai embeddings returned {len(vectors)} vectors for {len(texts)} inputs")
        if any(len(vector) != self.settings.embedding_dim for vector in vectors):
            raise ValueError(f"embedding dimension does not match settings.embedding_dim={self.settings.embedding_dim}")
        usage = self._usage(data, self.settings.embedding_model, "prompt_tokens", "total_tokens")
        return vectors, Usage(usage.model, usage.input_tokens, 0)


class _LLMRouter:
    def __init__(self, complete_client: LLM, chat_client: LLM) -> None:
        self.complete_client = complete_client
        self.chat_client = chat_client

    def complete_json(self, system: str, user: str, output_model: type[T], max_tokens: int) -> tuple[T, Usage]:
        return self.complete_client.complete_json(system, user, output_model, max_tokens)

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, Usage]:
        return self.chat_client.chat(system, messages, max_tokens)


def _make_model_client(settings: Settings, model: str) -> LLM:
    if provider_for(model) == "openai":
        return OpenAILLM(settings, model)
    return AnthropicLLM(settings, model)


def make_llm(settings: Settings) -> LLM:
    simulator_model = settings.simulator_model or settings.llm_model
    return _LLMRouter(
        complete_client=_make_model_client(settings, settings.llm_model),
        chat_client=_make_model_client(settings, simulator_model),
    )
