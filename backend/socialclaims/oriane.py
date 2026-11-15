"""Minimal Oriane Integration Connect client (`POST /rest/contents/search`, `POST /rest/profiles/search`).

The API key is sent only in the Authorization header and never logged; logs keep Oriane's `requestId` only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import structlog

from .config import ORIANE_CREDITS_PER_ROW

log = structlog.get_logger(__name__)


class OrianeError(RuntimeError):
    def __init__(self, status: int, message: str, request_id: str | None = None, body: dict | None = None):
        super().__init__(f"Oriane {status}: {message}")
        self.status, self.request_id, self.body = status, request_id, body or {}


class CreditsExhausted(OrianeError):
    """402: stop the run."""


class InvalidQuery(OrianeError):
    """400: bad filter."""


class OffsetPastEnd(OrianeError):
    """416: offset is past the end of the result set."""


@dataclass
class OrianeResponse:
    status: int
    body: dict
    request_id: str | None

    @property
    def partial(self) -> bool:
        return self.status == 206

    @property
    def results(self) -> list[dict]:
        return list((self.body.get("data") or {}).get("results") or [])

    @property
    def pagination(self) -> dict:
        return (self.body.get("metadata") or {}).get("pagination") or {}


def credits_for(rows: int, projection: str) -> int:
    return rows * ORIANE_CREDITS_PER_ROW[projection]


class OrianeClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str | None, base_url: str = "https://connect.oriane.xyz", timeout: float = 60.0,
                 max_retries: int = 4, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not api_key:
            raise RuntimeError("ORIANE_API_KEY is not set")
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        self.max_retries = max_retries
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, params: dict, body: dict) -> OrianeResponse:
        attempt = 0
        while True:
            try:
                r = self._http.post(path, params=params, json=body)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise OrianeError(0, f"transport error: {type(exc).__name__}") from exc
                attempt += 1
                self._sleep(min(2 ** attempt, 30))
                continue
            try:
                data = r.json()
            except ValueError:
                data = {}
            rid = (data.get("metadata") or {}).get("requestId")
            if r.status_code in (200, 206):
                log.info("oriane.call", path=path, status=r.status_code, request_id=rid,
                         rows=len((data.get("data") or {}).get("results") or []))
                return OrianeResponse(r.status_code, data, rid)
            message = str((data.get("error") or {}).get("message") or r.reason_phrase)
            log.warning("oriane.error", path=path, status=r.status_code, request_id=rid)
            if r.status_code in self.RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                self._sleep(min(2 ** attempt, 30))
                continue
            cls = {402: CreditsExhausted, 400: InvalidQuery, 416: OffsetPastEnd}.get(r.status_code, OrianeError)
            raise cls(r.status_code, message, rid, data)

    def search_contents(self, body: dict, *, offset: int = 0, limit: int = 100, projection: str = "full",
                        sort: str = "publishedAt:desc") -> OrianeResponse:
        if not 1 <= limit <= 100:
            raise ValueError("Oriane limit must be 1..100")
        params = {"offset": offset, "limit": limit, "projection": projection, "sort": sort}
        return self._post("/rest/contents/search", params, body)

    def search_profiles(self, body: dict, *, offset: int = 0, limit: int = 10, projection: str = "default",
                        sort: str = "followersCount:desc") -> OrianeResponse:
        params = {"offset": offset, "limit": limit, "projection": projection, "sort": sort}
        return self._post("/rest/profiles/search", params, body)
