"""HTTP clients for Anthropic JSON/chat and OpenAI embeddings."""

from __future__ import annotations

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

from .config import Settings

logger = logging.getLogger(__name__)
_warned_unknown_models: set[str] = set()
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """An LLM request failed or returned invalid structured output."""


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
            model=data.get("model") or model,
            input_tokens=int(usage.get(input_key) or 0),
            output_tokens=int(usage.get(output_key) or 0),
        )


class AnthropicLLM(_HTTPClient):
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(settings, sleep)
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        self.client = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
            transport=transport,
            timeout=60.0,
        )

    def complete_json(self, system: str, user: str, output_model: type[T], max_tokens: int) -> tuple[T, Usage]:
        tool_name = output_model.__name__
        payload = {
            "model": self.settings.llm_model,
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
        for retry_index in range(self.settings.llm_max_retries + 1):
            response = self._post(self.client, "/v1/messages", payload, "anthropic")
            data = response.json()
            usage = self._usage(data, self.settings.llm_model, "input_tokens", "output_tokens")
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
                    return output_model.model_validate(tool_block.get("input")), usage
                except ValidationError as exc:
                    last_error = exc
            if retry_index < self.settings.llm_max_retries:
                self.sleep(self._delay(retry_index))
        raise LLMError(
            f"anthropic response did not validate after {self.settings.llm_max_retries} retries"
        ) from last_error

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, Usage]:
        payload = {
            "model": self.settings.simulator_model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        response = self._post(self.client, "/v1/messages", payload, "anthropic")
        data = response.json()
        text = "\n".join(
            block["text"] for block in data.get("content", []) if block.get("type") == "text" and "text" in block
        )
        return text, self._usage(
            data, self.settings.simulator_model or self.settings.llm_model, "input_tokens", "output_tokens"
        )


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
            base_url="https://api.openai.com",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            transport=transport,
            timeout=60.0,
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
