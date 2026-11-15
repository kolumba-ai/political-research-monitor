import json

import pytest
from pydantic import ValidationError

from polgraph.schemas import (ContactX, DivisionsX, IndexAssessmentX, NationalStructureX, OfficeholderX, OfficeX,
                              SelectionMethod, agent_schema, normalize_classification, normalize_date, office_is_elected)


@pytest.mark.parametrize("raw,expected", [
    ("2024-10-01", "2024-10-01"), ("2024", "2024"), ("2024-10", "2024-10"), ("1 de octubre de 2024", "2024-10-01"),
    ("October 1, 2024", "2024-10-01"), ("diciembre 2024", "2024-12"), ("present", None), ("unknown", None),
    ("2024-13-01", None), ("2024-02-30", None), ("soon", None), (None, None),
])
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("name", ["Unknown", "N/A", "vacant", "TBD", "Cher", ""])
def test_officeholder_rejects_placeholders(name):
    with pytest.raises(ValidationError):
        OfficeholderX(full_name=name)


def test_officeholder_cleans_fields():
    h = OfficeholderX(full_name="  Claudia   Sheinbaum Pardo ", party_name="N/A", source_urls=["not a url", "https://gob.mx/x"])
    assert h.full_name == "Claudia Sheinbaum Pardo" and h.party_name is None and h.source_urls == ["https://gob.mx/x"]


def test_office_drops_bad_holders_but_keeps_office():
    o = OfficeX(title_en="Governor", selection_method="DIRECT_ELECTION",
                current_holders=[{"full_name": "unknown"}, {"full_name": "Pablo Lemus Navarro"}])
    assert o.selection_method == "direct_election" and [h.full_name for h in o.current_holders] == ["Pablo Lemus Navarro"]


def test_unknown_selection_method_maps_to_unknown():
    assert OfficeX(title_en="X", selection_method="lottery").selection_method == "unknown"


def test_elected_office_is_about_the_office_not_candidate_eligibility():
    assert office_is_elected(SelectionMethod.direct_election) is True
    assert office_is_elected("indirect_election") is True
    assert office_is_elected("appointment") is False   # e.g. cabinet secretary: appointed even if appointee once ran
    assert office_is_elected("mixed") is None           # needs per-seat detail
    assert office_is_elected(None) is None


def test_index_normalisation_and_labels():
    assert IndexAssessmentX(index="Varieties of Democracy (V-Dem)", classification_raw="x").index == "vdem"
    with pytest.raises(ValidationError):
        IndexAssessmentX(index="Some Blog Index", classification_raw="x")
    assert normalize_classification("freedom_house", "Partly Free") == ("partly_free", False)
    assert normalize_classification("freedom_house", "Not Free") == ("not_free", False)
    assert normalize_classification("freedom_house", "Free") == ("free", False)
    assert normalize_classification("vdem", "Electoral Autocracy (upper bound)") == ("electoral_autocracy", True)
    assert normalize_classification("vdem", "something else")[0] is None


def test_contact_enums_enforced():
    with pytest.raises(ValidationError):
        ContactX(dimensions=[{"purpose": "spam", "channel": "email", "sender": "citizen", "recipient": "any",
                              "status": "allowed"}])


def test_division_with_bad_holder_keeps_division():
    d = DivisionsX.model_validate({"divisions": [{"name": "Jalisco", "executive_title_en": "Governor",
                                                  "executive_selection_method": "direct_election",
                                                  "officeholder": {"full_name": "N/A"}}]})
    assert d.divisions[0].officeholder is None


@pytest.mark.parametrize("model", [NationalStructureX, DivisionsX, ContactX])
def test_agent_schema_is_self_contained(model):
    s = agent_schema(model)
    text = json.dumps(s)
    assert "$ref" not in text and "$defs" not in text and s["type"] == "object"
    assert "required" in s
