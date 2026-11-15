"""OpenAI chat-completions client with the same `extract(system, prompt, tool)` interface as System B's AnthropicLLM."""
from __future__ import annotations

import json
import time

import httpx

from polgraph.exa_gateway import PgRateLimiter, retry_after_seconds


class LLMError(RuntimeError):
    """A failed call that was still billed; `usage` lets the caller count its tokens."""

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


class OpenAILLM:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com",
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep, max_tokens: int = 2000,
                 rate_limiter_database_url: str | None = None, requests_per_second: float = 5.0, limiter=None):
        self.model, self.max_tokens, self.sleep = model, max_tokens, sleep
        self.limiter = limiter if limiter is not None else (
            PgRateLimiter(rate_limiter_database_url, f"openai:{api_key}", requests_per_second)
            if rate_limiter_database_url else None)
        self.http = httpx.Client(base_url=base_url, timeout=180, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def extract(self, system: str, prompt: str, tool: dict) -> tuple[dict, dict]:
        body = {"model": self.model, "max_completion_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": tool.get("name", "output"), "schema": tool["input_schema"], "strict": True}}}
        for attempt in range(4):
            try:
                if self.limiter is not None:
                    self.limiter.wait()
                r = self.http.post("/v1/chat/completions", json=body)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                self.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                delay = retry_after_seconds(r.headers) if r.status_code in (429, 503) else None
                self.sleep(2 ** attempt * 5 if delay is None else delay)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"openai {r.status_code}: {r.text[:500]}")
            data = r.json()
            choice = data["choices"][0]
            u = data.get("usage") or {}
            usage = {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}
            if choice.get("finish_reason") == "length":
                raise LLMError("openai output truncated at max_completion_tokens", usage)
            try:
                return json.loads(choice["message"]["content"]), usage
            except (TypeError, ValueError) as exc:
                raise LLMError(f"openai returned invalid JSON: {exc}", usage) from exc
        raise RuntimeError("unreachable")
