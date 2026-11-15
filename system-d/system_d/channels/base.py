"""The synchronous/asynchronous channel contract used by the polling agent."""

from __future__ import annotations

from typing import Protocol

from ..models import Respondent


class Channel(Protocol):
    def start(self, respondent: Respondent, opening: str) -> str: ...

    def send(self, conversation_id: str, message: str) -> str | None: ...

    def close(self, conversation_id: str) -> None: ...
