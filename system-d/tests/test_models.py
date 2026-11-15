import pytest
from pydantic import ValidationError

from system_d.models import Persona, PollResult, QuestionScript


def test_question_script_rejects_empty_fields_and_more_than_225_words():
    with pytest.raises(ValidationError):
        QuestionScript(opening="", belief_q="b", sentiment_q="s", open_q="o", closing="c")
    long_text = "palabra " * 226
    with pytest.raises(ValidationError, match="225 words"):
        QuestionScript(opening=long_text, belief_q="b", sentiment_q="s", open_q="o", closing="c")


def test_persona_validates_requested_demographic_ranges():
    with pytest.raises(ValidationError):
        Persona(
            name="Persona",
            age=17,
            state="Oaxaca",
            municipality_type="urbano",
            occupation="maestra",
            education="preparatoria",
            media_diet="radio",
            political_leaning="indecisa",
            trust_in_institutions="media",
            speaking_style="breve",
        )


def test_poll_result_accepts_null_answers_and_typed_transcript():
    result = PollResult(
        believes_claim=None,
        sentiment=None,
        completed=False,
        transcript=[{"role": "agent", "text": "Hola"}, {"role": "respondent", "text": "No sé"}],
    )
    assert result.transcript[1].role == "respondent"
    with pytest.raises(ValidationError):
        PollResult(
            believes_claim=None,
            sentiment=1.1,
            completed=False,
            transcript=[],
        )
