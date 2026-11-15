from dataclasses import replace

from system_d.agents.persona import generate
from system_d.agents.question_designer import design, ensure_question_set, queue_rewrite
from system_d.agents.sampling import select_respondents
from system_d.config import Settings
from system_d.llm import Usage
from system_d.models import Persona, PersonaBatch, QuestionScript, Respondent
from system_d.prompts import QUESTION_DESIGNER_SYSTEM, QUESTION_REWRITE_SUFFIX


def script(text="¿Cree usted que la afirmación es cierta?"):
    return QuestionScript(
        opening="Hola, esta encuesta es voluntaria.",
        belief_q=text,
        sentiment_q="¿Cómo se siente sobre la política?",
        open_q="¿Por qué respondió así?",
        closing="Gracias por su tiempo.",
    )


def persona(name):
    return Persona(
        name=name,
        age=36,
        state="Oaxaca",
        municipality_type="semiurbano",
        occupation="comerciante",
        education="preparatoria",
        media_diet="radio y Facebook",
        political_leaning="indecisa",
        trust_in_institutions="media",
        speaking_style="directa y breve",
    )


class QuestionLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return script(f"Pregunta versión {len(self.calls)}"), Usage("claude-sonnet-5-5", 10, 5)


class QuestionStore:
    def __init__(self):
        self.latest = None
        self.added = []

    def latest_question_set(self, claim_id):
        return self.latest

    def get_claim_info(self, claim_id):
        return {
            "id": claim_id,
            "politician_name": "Clara Brugada Molina",
            "party": "Morena",
            "region": "Ciudad de México",
            "context": {"office": "Jefa de Gobierno"},
            "canonical_text": "La ciudad abrirá nuevos espacios comunitarios.",
        }

    def add_question_set(self, claim_id, version, script_data):
        self.latest = {"id": f"q{version}", "version": version, "script": script_data}
        self.added.append((claim_id, version, script_data))
        return self.latest


def test_design_appends_verbatim_rewrite_suffix_and_previous_script():
    llm = QuestionLLM()
    previous = script()
    designed = design(
        llm,
        {"politician_name": "Clara Brugada Molina", "party": "Morena", "region": "CDMX", "context": {}},
        "Claim",
        previous,
    )
    assert designed.belief_q == "Pregunta versión 1"
    assert llm.calls[0]["system"].startswith(QUESTION_DESIGNER_SYSTEM)
    assert QUESTION_REWRITE_SUFFIX.split("{previous_script}")[0].strip() in llm.calls[0]["system"]
    assert previous.opening in llm.calls[0]["system"]


def test_question_set_creation_is_idempotent_and_rewrite_increments_version():
    store = QuestionStore()
    llm = QuestionLLM()
    first = ensure_question_set(store, llm, "aggregate-1")
    again = ensure_question_set(store, llm, "aggregate-1")
    revised = queue_rewrite(store, llm, "aggregate-1")
    assert first["version"] == 1
    assert again["id"] == first["id"]
    assert revised["version"] == 2
    assert [entry[1] for entry in store.added] == [1, 2]


class PersonaLLM:
    def __init__(self):
        self.calls = []
        self.batches = [
            PersonaBatch(personas=[persona("Ana Pérez"), persona("Luis Díaz")]),
            PersonaBatch(personas=[persona("ana pérez"), persona("María López")]),
        ]

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.batches[len(self.calls) - 1], Usage("claude-sonnet-5-5", 8, 4)


def test_persona_generation_chunks_and_deduplicates_names():
    llm = PersonaLLM()
    result = generate(
        llm,
        {"region": "Centro", "age_band": "18-39", "gender": "F", "urban_rural": None},
        3,
        {"Another Name"},
        replace(Settings(), personas_per_call=2),
    )
    assert [item.name for item in result] == ["Ana Pérez", "Luis Díaz", "María López"]
    assert len(llm.calls) == 2
    assert "Number of personas: 2" in llm.calls[0]["user"]
    assert "Another Name" in llm.calls[0]["user"]


class SamplingStore:
    def __init__(self):
        self.inserted = []
        self.available = [
            Respondent(
                id="existing",
                stratum_id="stratum-1",
                persona={"name": "Existing", "sim_run": "run-1"},
                channel="simulator",
            )
        ]

    def list_strata(self):
        return [{"id": "stratum-1", "region": "Centro", "age_band": "18-39", "gender": "F"}]

    def available_simulated_respondents(self, stratum_id, sim_run, aggregated_claim_id):
        return self.available

    def add_respondent(self, stratum_id, persona_data):
        self.inserted.append((stratum_id, persona_data))
        return Respondent("new", stratum_id, persona_data, "simulator")


def test_sampling_reuses_existing_respondents_and_generates_only_shortfall():
    store = SamplingStore()
    respondents = select_respondents(
        store,
        PersonaLLM(),
        "run-1",
        "aggregate-1",
        {"stratum-1": 2},
        replace(Settings(), personas_per_call=2),
    )
    assert [respondent.id for respondent in respondents] == ["existing", "new"]
    assert store.inserted[0][1]["sim_run"] == "run-1"
    assert store.inserted[0][1]["name"] == "Ana Pérez"
