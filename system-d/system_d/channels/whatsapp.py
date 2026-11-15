"""Phase-two WhatsApp channel stub."""

from __future__ import annotations

from ..models import Respondent


class WhatsAppChannel:
    def start(self, respondent: Respondent, opening: str) -> str:
        raise NotImplementedError("phase 2")

    def send(self, conversation_id: str, message: str) -> str | None:
        raise NotImplementedError("phase 2")

    def close(self, conversation_id: str) -> None:
        raise NotImplementedError("phase 2")
