import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))

import data


def test_status_label_shows_capped_as_inconclusive():
    assert data.status_label("capped") == "inconclusive (capped)"
    assert data.status_label("decisive") == "decisive"
    assert data.status_label(None) == ""


def test_share_and_sentiment_formatting():
    assert data.fmt_share_ci(0.642, 0.45, 0.79) == "64% (45%–79%)"
    assert data.fmt_share_ci(0.5, None, None) == "50%"
    assert data.fmt_share_ci(None, None, None) == "—"
    assert data.fmt_signed_ci(-0.24, -0.34, -0.14) == "-0.24 [-0.34, -0.14]"
    assert data.fmt_signed_ci(None, None, None) == "—"
    assert data.fmt_signed(0.1) == "+0.10"
    assert data.fmt_signed(None) == "—"


def test_gap_needs_both_values():
    assert data.gap(0.1, 0.25) == pytest.approx(-0.15)
    assert data.gap(None, 0.2) is None
    assert data.gap(0.2, None) is None


def test_belief_label():
    assert data.belief_label(True, True) == "sí"
    assert data.belief_label(False, True) == "no"
    assert data.belief_label(None, True) == "no sé / no answer"
    assert data.belief_label(True, False) == "incomplete"


def test_persona_and_stratum_labels():
    persona = {"name": "Lorena", "age": 38, "occupation": "Técnica", "state": "CDMX", "political_leaning": "Morena"}
    assert data.persona_summary(persona) == "Lorena, 38 años, Técnica, CDMX. Postura: Morena"
    assert data.persona_summary(None) == ""
    row = {"region": "Centro", "age_band": "18-39", "gender": "F", "urban_rural": None}
    assert data.stratum_label(row) == "Centro, 18-39, F"


def test_database_url_prefers_env_then_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env")
    assert data.database_url() == "postgresql://env"
    monkeypatch.delenv("DATABASE_URL")
    url_file = tmp_path / "url"
    url_file.write_text("postgresql://file\n")
    monkeypatch.setenv("DATABASE_URL_FILE", str(url_file))
    assert data.database_url() == "postgresql://file"
    monkeypatch.delenv("DATABASE_URL_FILE")
    with pytest.raises(RuntimeError):
        data.database_url()


def test_claim_scope_and_title_use_storyline_link():
    linked = {"storyline_id": "sl-1", "storyline_label": "Salud Casa por Casa", "canonical_text": "18 millones"}
    demo = {"storyline_id": None, "storyline_label": None, "canonical_text": "Demo claim"}
    assert [data.in_scope(c, "Real storylines") for c in (linked, demo)] == [True, False]
    assert [data.in_scope(c, "Demo seed") for c in (linked, demo)] == [False, True]
    assert [data.in_scope(c, "All") for c in (linked, demo)] == [True, True]
    assert data.claim_title(linked) == "Salud Casa por Casa"
    assert data.claim_title(demo) == "Demo claim"
