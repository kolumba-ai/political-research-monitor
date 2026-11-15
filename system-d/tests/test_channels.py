import uuid

from system_d.channels.simulator import SimulatorChannel
from system_d.channels.vapi import VapiChannel
from system_d.channels.whatsapp import WhatsAppChannel
from system_d.config import Settings
from system_d.llm import Usage
from system_d.models import Respondent
from system_d.prompts import BEHAVIOUR_NORMAL, BEHAVIOURS_DIFFICULT


class ChatLLM:
    def __init__(self):
        self.calls = []

    def chat(self, system, messages, max_tokens):
        self.calls.append((system, messages, max_tokens))
        return f"reply-{len(self.calls)}", Usage("claude-sonnet-5-5", 2, 1)


def respondent(identifier, sim_run="run-1"):
    return Respondent(
        id=identifier,
        stratum_id="stratum-1",
        persona={"name": "Persona", "sim_run": sim_run},
        channel="simulator",
    )


def test_simulator_records_opening_and_maps_agent_respondent_roles():
    llm = ChatLLM()
    channel = SimulatorChannel(llm, Settings(), rng_seed="seed", conversation_key="run-1|claim-1")
    conversation_id = channel.start(respondent("respondent-1"), "Opening message")
    uuid.UUID(conversation_id)
    assert llm.calls == []
    assert channel.send(conversation_id, "First question") == "reply-1"
    assert channel.send(conversation_id, "Second question") == "reply-2"
    assert llm.calls[0][2] == 1024
    messages = llm.calls[1][1]
    assert messages == [
        {"role": "user", "content": "Opening message"},
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "reply-1"},
        {"role": "user", "content": "Second question"},
    ]
    channel.close(conversation_id)
    assert channel.usage_for(conversation_id).input_tokens == 4


def test_simulator_behavior_is_deterministic_for_respondent_claim_and_run():
    channel = SimulatorChannel(ChatLLM(), Settings(), rng_seed="seed", conversation_key="run-1|claim-1")
    first_id = channel.start(respondent("respondent-17"), "Opening")
    first = channel.chosen_behaviour
    channel.close(first_id)
    second_id = channel.start(respondent("respondent-17"), "Opening")
    assert channel.chosen_behaviour == first
    assert first == BEHAVIOUR_NORMAL or first in BEHAVIOURS_DIFFICULT.values()
    channel.close(second_id)


def test_simulator_difficult_behavior_rate_is_about_twelve_percent():
    channel = SimulatorChannel(ChatLLM(), Settings(), rng_seed="seeded-run", conversation_key="run-1|claim-1")
    difficult = set(BEHAVIOURS_DIFFICULT.values())
    selected = 0
    for index in range(2000):
        conversation_id = channel.start(respondent(f"respondent-{index}"), "Opening")
        selected += channel.chosen_behaviour in difficult
        channel.close(conversation_id)
    rate = selected / 2000
    assert 0.09 <= rate <= 0.15


def test_simulator_prompt_excludes_bookkeeping_persona_fields():
    llm = ChatLLM()
    persona = {
        "name": "Ana Pérez",
        "age": 34,
        "state": "Puebla",
        "municipality_type": "urbano",
        "occupation": "docente",
        "education": "universidad",
        "media_diet": "radio y redes",
        "political_leaning": "centro",
        "trust_in_institutions": "media",
        "speaking_style": "directa",
        "sim_run": "run-1",
        "aggregated_claim_id": "claim-1",
    }
    channel = SimulatorChannel(llm, Settings(), rng_seed="seed", conversation_key="run-1|claim-1")
    conversation_id = channel.start(Respondent("respondent-1", "stratum-1", persona, "simulator"), "Opening")
    channel.send(conversation_id, "Question")
    system = llm.calls[0][0]
    assert '"name": "Ana Pérez"' in system
    assert '"age": 34' in system
    assert "sim_run" not in system
    assert "aggregated_claim_id" not in system
    channel.close(conversation_id)


def test_whatsapp_and_vapi_are_phase_two_stubs():
    person = respondent("respondent")
    for channel in (WhatsAppChannel(), VapiChannel()):
        for method, args in (
            (channel.start, (person, "Opening")),
            (channel.send, ("conversation", "Question")),
            (channel.close, ("conversation",)),
        ):
            try:
                method(*args)
            except NotImplementedError as exc:
                assert str(exc) == "phase 2"
            else:
                raise AssertionError("phase-two channel should not be implemented")
