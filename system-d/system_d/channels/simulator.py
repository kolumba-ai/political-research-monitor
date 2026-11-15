"""In-memory respondent simulator with deterministic difficult-behaviour sampling."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Settings
from ..llm import LLM, Usage, UsageMeter
from ..models import Persona, Respondent
from ..prompts import BEHAVIOUR_NORMAL, BEHAVIOURS_DIFFICULT, RESPONDENT_SIMULATOR_SYSTEM


@dataclass
class _Conversation:
    respondent: Respondent
    history: list[dict[str, str]]
    behaviour: str
    usage: UsageMeter = field(default_factory=UsageMeter)


class SimulatorChannel:
    def __init__(
        self,
        llm: LLM,
        settings: Settings,
        rng_seed: str | int,
        conversation_key: str,
        token_budget: int = 512,
        hash_fn: Callable[[bytes], bytes] = hashlib.sha256,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.rng_seed = str(rng_seed)
        self.conversation_key = conversation_key
        self.token_budget = token_budget
        self.hash_fn = hash_fn
        self._conversations: dict[str, _Conversation] = {}
        self._closed_usage: dict[str, Usage] = {}
        self.behaviours: dict[str, str] = {}
        self.chosen_behaviour: str | None = None

    def _choose_behaviour(self, respondent: Respondent) -> str:
        source = f"{self.rng_seed}|{respondent.id}|{self.conversation_key}".encode()
        digest = self.hash_fn(source).digest()
        sample = int.from_bytes(digest[:8], "big") / 2**64
        if sample >= 0.12:
            return BEHAVIOUR_NORMAL
        difficult = sorted(BEHAVIOURS_DIFFICULT.items())
        choice = int.from_bytes(digest[8:16], "big") % len(difficult)
        return difficult[choice][1]

    def start(self, respondent: Respondent, opening: str) -> str:
        conversation_id = str(uuid.uuid4())
        behaviour = self._choose_behaviour(respondent)
        self._conversations[conversation_id] = _Conversation(
            respondent=respondent,
            history=[{"role": "agent", "content": opening}],
            behaviour=behaviour,
        )
        self.behaviours[conversation_id] = behaviour
        self.chosen_behaviour = behaviour
        return conversation_id

    def send(self, conversation_id: str, message: str) -> str:
        conversation = self._conversations[conversation_id]
        conversation.history.append({"role": "agent", "content": message})
        messages = [
            {"role": "user" if item["role"] == "agent" else "assistant", "content": item["content"]}
            for item in conversation.history
        ]
        persona_fields = conversation.respondent.persona
        persona = json.dumps(
            {name: persona_fields[name] for name in Persona.model_fields if name in persona_fields},
            ensure_ascii=False,
        )
        system = RESPONDENT_SIMULATOR_SYSTEM.format(persona=persona, behaviour=conversation.behaviour)
        reply, usage = self.llm.chat(system, messages, max_tokens=self.token_budget)
        conversation.usage.add(usage)
        conversation.history.append({"role": "respondent", "content": reply})
        self.chosen_behaviour = conversation.behaviour
        return reply

    def usage_for(self, conversation_id: str) -> Usage:
        conversation = self._conversations.get(conversation_id)
        if conversation is not None:
            return conversation.usage.usage
        return self._closed_usage.get(conversation_id, Usage("mixed", 0, 0))

    def close(self, conversation_id: str) -> None:
        conversation = self._conversations.pop(conversation_id, None)
        if conversation is not None:
            self._closed_usage[conversation_id] = conversation.usage.usage
