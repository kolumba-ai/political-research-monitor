import pytest
from psycopg.rows import dict_row

import system_d.agents.polling as polling_module
from system_d.agents.polling import poll_task, run_poll
from system_d.channels.simulator import SimulatorChannel
from system_d.config import Settings
from system_d.llm import Usage
from system_d.models import PollScore, QuestionScript, Respondent

SCRIPT = QuestionScript(
    opening="Hola, esta encuesta es voluntaria.",
    belief_q="¿Cree que el programa llegará este año?",
    sentiment_q="¿Cómo se siente sobre la política?",
    open_q="¿Por qué respondió así?",
    closing="Gracias por participar.",
)


class FakeChannel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.sent = []
        self.closed = False

    def start(self, respondent, opening):
        self.opening = opening
        return "conversation-id"

    def send(self, conversation_id, message):
        self.sent.append(message)
        return next(self.replies)

    def close(self, conversation_id):
        self.closed = True

    def usage_for(self, conversation_id):
        return Usage("claude-sonnet-5-5", 4, 2)


class ScoringLLM:
    def __init__(self, believes="yes", choice=4, refused=False, completed=True):
        self.believes = believes
        self.choice = choice
        self.refused = refused
        self.completed = completed
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return (
            PollScore(
                believes_claim=self.believes,
                sentiment_choice=self.choice,
                refused=self.refused,
                completed=self.completed,
            ),
            Usage("claude-sonnet-5-5", 5, 3),
        )


def respondent():
    return Respondent("respondent-1", "stratum-1", {"name": "Persona"}, "simulator")


def test_polling_normal_flow_has_three_respondent_turns_and_maps_score():
    channel = FakeChannel(["Sí", "Positiva", "Porque me parece útil"])
    llm = ScoringLLM(believes="yes", choice=4)
    result, usage = run_poll(channel, respondent(), SCRIPT, "Politician", "Claim", llm)
    assert channel.sent == [SCRIPT.belief_q, SCRIPT.sentiment_q, f"{SCRIPT.open_q}\n\n{SCRIPT.closing}"]
    assert channel.closed
    assert result.believes_claim is True
    assert result.sentiment == 0.5
    assert result.completed
    assert result.transcript[0].text == SCRIPT.opening
    assert usage.input_tokens == 9
    assert usage.output_tokens == 5


def test_polling_stops_after_accent_insensitive_refusal():
    channel = FakeChannel(["No me interesa participar."])
    llm = ScoringLLM(believes=None, choice=None, refused=True, completed=False)
    result, _ = run_poll(channel, respondent(), SCRIPT, "Politician", "Claim", llm)
    assert channel.sent == [SCRIPT.belief_q]
    assert channel.closed
    assert not result.completed
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    ("choice", "expected"),
    [(1, -1.0), (2, -0.5), (3, 0.0), (4, 0.5), (5, 1.0)],
)
def test_polling_maps_all_five_sentiment_choices(choice, expected):
    result, _ = run_poll(
        FakeChannel(["sí", "neutral", "respuesta"]),
        respondent(),
        SCRIPT,
        "Politician",
        "Claim",
        ScoringLLM(choice=choice),
    )
    assert result.sentiment == expected


def test_polling_treats_dont_know_as_answered_belief():
    result, _ = run_poll(
        FakeChannel(["No sé", "Neutral", "No tengo una razón"]),
        respondent(),
        SCRIPT,
        "Politician",
        "Claim",
        ScoringLLM(believes="dont_know", choice=3),
    )
    assert result.believes_claim is None
    assert result.completed


def test_polling_stops_when_channel_returns_none():
    channel = FakeChannel([None])
    result, _ = run_poll(
        channel,
        respondent(),
        SCRIPT,
        "Politician",
        "Claim",
        ScoringLLM(believes=None, choice=None, completed=False),
    )
    assert channel.sent == [SCRIPT.belief_q]
    assert channel.closed
    assert len(result.transcript) == 2
    assert result.transcript[1].text == SCRIPT.belief_q
    assert not result.completed


class WorkerLLM(ScoringLLM):
    def __init__(self):
        super().__init__()
        self.chat_systems = []

    def chat(self, system, messages, max_tokens):
        self.chat_systems.append(system)
        return "Creo que sí, y me parece positiva.", Usage("claude-sonnet-5-5", 3, 2)


class PollTaskStore:
    def __init__(self):
        self.inserted = None
        self.respondent = Respondent(
            "respondent-id",
            "stratum-1",
            {"name": "Persona", "sim_run": "run-1"},
            "simulator",
        )

    def get_claim_info(self, claim_id):
        return {"politician_name": "Politician", "canonical_text": "Claim"}

    def get_question_set_by_id(self, question_set_id):
        return {"id": question_set_id, "script": SCRIPT.model_dump()}

    def get_respondent(self, respondent_id):
        return self.respondent

    def insert_poll_response(self, *args):
        self.inserted = args


def test_poll_task_persists_response_and_returns_usage(monkeypatch):
    store = PollTaskStore()
    llm = WorkerLLM()
    conversation_keys = []

    def simulator(llm_arg, settings_arg, **kwargs):
        conversation_keys.append(kwargs["conversation_key"])
        return SimulatorChannel(llm_arg, settings_arg, **kwargs)

    monkeypatch.setattr(polling_module, "SimulatorChannel", simulator)
    result = poll_task(
        {
            "aggregated_claim_id": "claim-id",
            "question_set_id": "question-id",
            "respondent_id": "respondent-id",
            "sim_run": "run-1",
            "batch_no": 2,
        },
        Settings(),
        store=store,
        llm=llm,
    )
    assert result["ok"]
    assert result["completed"]
    assert result["input_tokens"] == 14
    assert result["output_tokens"] == 9
    assert store.inserted[:5] == ("claim-id", "respondent-id", "question-id", "run-1", 2)
    assert "sim_run" not in llm.chat_systems[0]
    assert "aggregated_claim_id" not in llm.chat_systems[0]
    assert "aggregated_claim_id" not in store.respondent.persona
    assert conversation_keys == ["run-1|claim-id"]


@pytest.mark.parametrize("fail_poll", [False, True])
def test_poll_task_uses_one_configured_connection_and_closes_it(monkeypatch, fail_poll):
    class FakeConnection:
        row_factory = None
        closed = False

        def close(self):
            self.closed = True

    conn = FakeConnection()
    connect_calls = []
    configured = []
    poll_calls = []

    def connect(database_url, *, row_factory):
        connect_calls.append((database_url, row_factory))
        return conn

    def configure(connection):
        configured.append(connection)

    def poll_with_store(task, settings, store, llm):
        poll_calls.append(store.conn)
        if fail_poll:
            raise RuntimeError("poll failed")
        return {"ok": True}

    monkeypatch.setattr(polling_module.psycopg, "connect", connect)
    monkeypatch.setattr(polling_module, "configure_connection", configure)
    monkeypatch.setattr(polling_module, "_poll_task_with_store", poll_with_store)
    settings = Settings(database_url="test-database-url", anthropic_api_key="test-key")
    task = {}

    if fail_poll:
        with pytest.raises(RuntimeError, match="poll failed"):
            poll_task(task, settings, llm=WorkerLLM())
    else:
        assert poll_task(task, settings, llm=WorkerLLM()) == {"ok": True}

    assert connect_calls == [("test-database-url", dict_row)]
    assert configured == [conn]
    assert poll_calls == [conn]
    assert conn.closed
