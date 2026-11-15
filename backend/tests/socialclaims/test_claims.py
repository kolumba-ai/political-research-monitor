import re
import json
import httpx
import pytest

from socialclaims import accounts
from socialclaims.capture import Capturer
from socialclaims.claims import AnthropicLLM, Extractor, _numbers, _prop_id, align, build_prompt
from socialclaims.segments import process_politician

from .conftest import FIXED_NOW, FakeOriane, make_result

PID = "person:MX:claudia-sheinbaum-pardo"
PROP = {"subject": "desempleo en México", "predicate": "bajó a", "negated": False,
        "values": [{"value": "2.5", "unit": "%"}], "place": "México", "qualifiers": []}


def claim(quote, refs, text=None, **kw):
    c = {"segment_ids": refs, "quote": quote, "text": text or quote, "text_en": "x", "type": "statistic",
         "modality": "asserted", "relation_to_politician": "speaker", "topics": ["empleo_salarios"],
         "entities": {}, "checkworthiness": 0.9, "proposition": dict(PROP),
         "referenced_period": {"start": "2026-07-01", "end": "2026-09-30"}}
    c.update(kw)
    return c


def extract_claims(conn, graph, sc_settings, quote, claims_for_ref, extra_segment=None, post_id=88):
    accounts.approve(conn, PID, "tiktok", "claudiasheinbaum", "tester", ["https://www.gob.mx"], "prf_own")
    post = speech_post(post_id, quote, profileId="prf_own", profileHandle="claudiasheinbaum")
    if extra_segment:
        post["media"][0]["transcriptChunks"].append(
            {"startSeconds": "6.0", "endSeconds": "9.0", "text": extra_segment}
        )
    Capturer(conn, graph, FakeOriane([post]).client(), sc_settings, clock=lambda: FIXED_NOW).capture(PID)
    process_politician(conn, graph, sc_settings.raw_storage_dir, PID, now=FIXED_NOW)
    llm = FakeLLM(lambda prompt: claims_for_ref(ref(prompt, "(speech")))
    out = Extractor(conn, graph, llm).run(PID, 1)
    return out, conn.execute("SELECT * FROM distilled_claim").fetchall()


class FakeLLM:
    model = "fake-claude"

    def __init__(self, script):
        self.script, self.prompts = script, []

    def extract(self, system, prompt, tool):
        self.prompts.append(prompt)
        out = self.script(prompt)
        if isinstance(out, Exception):
            raise out
        return {"claims": out}, {"input_tokens": 100, "output_tokens": 20}


def speech_post(i, text, **kw):
    media = [{"type": "video", "transcriptLanguage": "es",
              "transcriptChunks": [{"startSeconds": "1.0", "endSeconds": "5.0", "text": text}], "frames": []}]
    return make_result(i, media=media, popularComments=[{"content": "COMENTARIO SECRETO"}], **kw)


@pytest.fixture
def segmented(conn, graph, sc_settings):
    accounts.approve(conn, PID, "tiktok", "claudiasheinbaum", "tester", ["https://www.gob.mx"], "prf_own")
    posts = [speech_post(1, "En el tercer trimestre el desempleo bajó a 2.5 por ciento",
                         profileId="prf_own", profileHandle="claudiasheinbaum"),
             speech_post(2, "Claudia Sheinbaum dice que el desempleo bajó a 2.5 por ciento")]
    Capturer(conn, graph, FakeOriane(posts).client(), sc_settings, clock=lambda: FIXED_NOW).capture(PID)
    process_politician(conn, graph, sc_settings.raw_storage_dir, PID, now=FIXED_NOW)
    return posts


def ref(prompt, needle):
    line = next(x for x in prompt.splitlines() if needle in x)
    return line[1:line.index("]")]


def test_align_exact_fuzzy_none():
    assert align("bajó  a 2.5", "El desempleo BAJÓ a 2.5 por ciento")[:3] == ("exact", 13, 23)
    assert align("el desempleo bajo a 2.5 por ciento", "El desempleo bajó a 2.5 por ciento")[0] == "fuzzy"
    assert align("subió la inflación", "El desempleo bajó a 2.5 por ciento")[0] == "none"


def test_grounding_rules_and_speaker_evidence(conn, graph, segmented):
    def script(prompt):
        r = ref(prompt, "(speech")
        own = "tercer trimestre" in prompt
        if own:
            return [claim("el desempleo bajó a 2.5 por ciento", [r]),
                    claim("el desempleo bajó a 2.5 por ciento", [r], relation_to_politician="subject",
                          text="el desempleo bajó a 2.5 por ciento (subject)"),
                    claim("el desempleo bajó a 2.5 por ciento", [r], text="El desempleo bajó a 3.5 por ciento"),
                    claim("el desempleo bajó a 2.5 por ciento", [r], text="El desempleo no bajó a 2.5 por ciento"),
                    claim("la inflación subió a 9 por ciento", [r])]
        return [claim("el desempleo bajó a 2.5 por ciento", [r], relation_to_politician="speaker")]
    llm = FakeLLM(script)
    out = Extractor(conn, graph, llm).run(PID, 10)
    assert (out["extracted"], out["claims"]) == (2, 6)
    assert all("COMENTARIO SECRETO" not in p for p in llm.prompts)
    rows = conn.execute("SELECT * FROM distilled_claim ORDER BY oriane_id, claim_id").fetchall()
    by = {(r["oriane_id"][-1], r["text"]): r for r in rows}
    good = by[("1", "el desempleo bajó a 2.5 por ciento")]
    assert good["grounding"] == "exact" and good["speaker_status"] == "probable" and good["speaker_id"] == PID
    assert good["relation_to_politician"] == "speaker" and (good["quote_start_s"], good["quote_end_s"]) == (1.0, 5.0)
    assert str(good["referenced_period_start"]) == "2026-07-01"
    assert by[("1", "El desempleo bajó a 3.5 por ciento")]["grounding"] == "review"
    subj = by[("1", "el desempleo bajó a 2.5 por ciento (subject)")]
    assert subj["speaker_id"] is None and subj["speaker_status"] == "unknown"
    assert "negation_mismatch" in by[("1", "El desempleo no bajó a 2.5 por ciento")]["grounding_issues"]
    assert "paraphrase_numbers_not_in_quote:3.5" in by[("1", "El desempleo bajó a 3.5 por ciento")]["grounding_issues"]
    assert by[("1", "la inflación subió a 9 por ciento")]["grounding"] == "failed"
    third = by[("2", "el desempleo bajó a 2.5 por ciento")]
    assert third["relation_to_politician"] == "mentioned" and third["speaker_id"] is None
    assert "speaker_relation_without_evidence" in third["grounding_issues"]
    # Same proposition across two posts: one proposition, separate assertions.
    assert good["proposition_id"] == third["proposition_id"]
    assert conn.execute("SELECT count(*) AS n FROM proposition").fetchone()["n"] == 1


def test_same_topic_different_figures_or_period_are_different_propositions(conn, graph, segmented):
    def script(prompt):
        r = ref(prompt, "(speech")
        return [claim("el desempleo bajó a 2.5 por ciento", [r]),
                claim("el desempleo bajó a 2.5 por ciento", [r], referenced_period={"start": "2026-04-01",
                                                                                  "end": "2026-06-30"})]
    Extractor(conn, graph, FakeLLM(script)).run(PID, 1)
    rows = conn.execute("SELECT topics, proposition_id FROM distilled_claim").fetchall()
    assert len({r["proposition_id"] for r in rows}) == 2 and all(r["topics"] == ["empleo_salarios"] for r in rows)


def test_idempotent_failures_and_versioning(conn, graph, segmented):
    calls = []
    llm = FakeLLM(lambda p: calls.append(1) or (RuntimeError("boom") if len(calls) == 1 else []))
    ex = Extractor(conn, graph, llm)
    first = ex.run(PID, 10)
    assert (first["failed"], first["extracted"]) == (1, 1)
    retry = ex.run(PID, 10)
    assert (retry["pending"], retry["extracted"]) == (1, 1)
    assert ex.run(PID, 10)["pending"] == 0
    other = FakeLLM(lambda p: [])
    other.model = "fake-claude-2"
    assert Extractor(conn, graph, other).run(PID, 10)["extracted"] == 2


def test_anthropic_client_uses_structured_output():
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"claims": []}'}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 1}})
    llm = AnthropicLLM("k", "m", transport=httpx.MockTransport(handler))
    result, usage = llm.extract("sys", "prompt", {"name": "record_claims", "input_schema": {}})
    assert result == {"claims": []} and usage["input_tokens"] == 5 and seen["key"] == "k"
    body = json.loads(seen["body"])
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": {}} and "temperature" not in body


def test_decimal_comma_is_not_a_thousands_separator():
    assert _numbers("2,5% y 25% y 1,250,000 y 3.7.") == {"2.5", "25", "1250000", "3.7"}
    a, b = ({**PROP, "values": [{"value": v, "unit": "%"}]} for v in ("2,5", "25"))
    assert _prop_id(a, (None, None)) != _prop_id(b, (None, None))


@pytest.mark.parametrize(("text", "expected"), [
    ("1.700 reservas", "1700"),
    ("260.000", "260000"),
    ("6 mil 500 millones", "6500"),
    ("120 mil personas", "120000"),
    ("dos puentes", "2"),
    ("ninguna abstención", "0"),
    ("10.6 billones", "10.6"),
    ("2,5%", "2.5"),
    ("1,250,000", "1250000"),
])
def test_spanish_number_canonicalization(text, expected):
    assert expected in _numbers(text)


@pytest.mark.parametrize(("quote", "text"), [
    ("No, Sheinbaum sale a defender el video", "Sheinbaum sale a defender el video"),
    ("cuidan a Andy y no a los mexicanos", "cuidan a Andy y no a los mexicanos"),
])
def test_discourse_and_contrastive_negation_ground_exactly(conn, graph, sc_settings, quote, text):
    prop = {**PROP, "predicate": "sale a defender", "negated": False, "values": []}
    out, rows = extract_claims(conn, graph, sc_settings, quote,
                               lambda r: [claim(quote, [r], text=text, proposition=prop)])

    assert out["grounding"] == {"exact": 1}
    assert rows[0]["grounding"] == "exact"
    assert "negation_mismatch" not in rows[0]["grounding_issues"]
    assert "proposition_negation_mismatch" not in rows[0]["grounding_issues"]


def test_predicate_negation_is_normalized_and_shares_proposition_id(conn, graph, sc_settings):
    quote = "No sale Sheinbaum a defender a las madres"
    normalized = {**PROP, "subject": "Sheinbaum", "predicate": "no sale a defender", "negated": False,
                  "values": []}
    canonical = {**normalized, "predicate": "sale a defender", "negated": True}
    out, rows = extract_claims(
        conn, graph, sc_settings, quote,
        lambda r: [claim(quote, [r], proposition=normalized), claim(quote, [r], proposition=canonical)],
    )
    adjusted = next(row for row in rows if "predicate_negation_normalized" in row["grounding_issues"])
    assert out["grounding"] == {"exact": 2}
    assert adjusted["grounding"] == "exact"
    assert adjusted["grounding_issues"] == ["predicate_negation_normalized"]
    assert {row["proposition_id"] for row in rows} == {adjusted["proposition_id"]}
    stored = conn.execute("SELECT predicate, negated FROM proposition").fetchone()
    assert (stored["predicate"], stored["negated"]) == ("sale a defender", True)


def test_negated_proposition_without_quote_negation_needs_review(conn, graph, sc_settings):
    quote = "Sheinbaum sale a defender a las madres"
    prop = {**PROP, "predicate": "sale a defender", "negated": True, "values": []}
    out, rows = extract_claims(conn, graph, sc_settings, quote,
                               lambda r: [claim(quote, [r], proposition=prop)])

    assert out["grounding"] == {"review": 1}
    assert rows[0]["grounding"] == "review"
    assert "proposition_negation_mismatch" in rows[0]["grounding_issues"]


def test_corrected_segment_id_is_informational(conn, graph, sc_settings):
    quote = "Sheinbaum anuncia nuevos apoyos"
    prop = {**PROP, "predicate": "anuncia nuevos apoyos", "values": []}
    out, rows = extract_claims(
        conn, graph, sc_settings, "Texto de otro segmento sin relación",
        lambda r: [claim(quote, [r], proposition=prop)], extra_segment=quote,
    )

    assert out["grounding"] == {"exact": 1}
    assert rows[0]["grounding"] == "exact"
    assert rows[0]["grounding_issues"] == ["segment_id_corrected"]


def test_scaled_value_and_dot_thousands_match_quote_and_paraphrase(conn, graph, sc_settings):
    quote = "Se destinarán 6 mil 500 millones de pesos"
    value_prop = {**PROP, "values": [{"value": "6500", "unit": "millones de pesos"}]}
    out, rows = extract_claims(conn, graph, sc_settings, quote,
                               lambda r: [claim(quote, [r], proposition=value_prop)])
    assert out["grounding"] == {"exact": 1}
    assert "proposition_values_not_in_quote" not in rows[0]["grounding_issues"]

    quote = "Hubo 1.700 reservas"
    paraphrase = "Hubo 1,700 reservas"
    prop = {**PROP, "values": []}
    out, rows = extract_claims(conn, graph, sc_settings, quote,
                               lambda r: [claim(quote, [r], text=paraphrase, proposition=prop)], post_id=89)
    assert out["grounding"] == {"exact": 1}
    assert not any(issue.startswith("paraphrase_numbers_not_in_quote") for issue in rows[0]["grounding_issues"])


def test_oversized_speech_does_not_hide_caption(graph):
    from socialclaims.systema import read_person
    segs = [{"source": "speech", "ordinal": 0, "text": "palabra " * 3000, "start_s": 0.0, "end_s": 9.0},
            {"source": "caption", "ordinal": 1, "text": "El desempleo bajó a 2.5%", "start_s": None, "end_s": None}]
    prompt, refs = build_prompt(read_person(graph, PID), {}, segs, {"topics": []})
    assert "El desempleo bajó a 2.5%" in prompt and set(refs) == {"s0", "s1"}
    segs[0]["text"] = "1234567 " * 2000
    prompt, _ = build_prompt(read_person(graph, PID), {}, segs, {"topics": []})
    assert "1234567 [...]" in prompt and not re.search(r"\b(?!1234567\b)\d+ \[\.\.\.\]", prompt)


def test_truncation_marker_does_not_eat_a_short_caption(graph):
    from socialclaims.systema import read_person
    for n in range(1, 9):
        segs = [{"source": "speech", "ordinal": 0, "text": "ab " * 5000 + "x" * n, "start_s": 0.0, "end_s": 9.0},
                {"source": "caption", "ordinal": 1, "text": "Bajó 2%", "start_s": None, "end_s": None}]
        prompt, refs = build_prompt(read_person(graph, PID), {}, segs, {"topics": []})
        assert "Bajó 2%" in prompt and "s1" in refs
