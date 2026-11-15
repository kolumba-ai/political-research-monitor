from polgraph.directories import mx_name_from_surname_first, parse_mx_deputies, parse_mx_senators
from polgraph.schemas import office_is_elected


def test_surname_first_names():
    assert mx_name_from_surname_first("Abreu Artiñano Rocío Adriana") == "Rocío Adriana Abreu Artiñano"
    assert mx_name_from_surname_first("De la Torre Valdez Carlos") == "Carlos De la Torre Valdez"
    assert mx_name_from_surname_first("García Juan") == "Juan García"


def test_parse_deputies_district_vs_list():
    text = ("| | | |\n| Diputado | Entidad | Distrito / Circunscripción |\n"
            "| 1 Abreu Artiñano Rocío Adriana | Campeche | Circ. 3 |\n| 2 Acosta Islas Anabel | Sonora | Dtto. 6 |\n")
    rows = parse_mx_deputies(text)
    assert [(r["kind"], r["number"], r["entity"]) for r in rows] == [("Circ", 3, "Campeche"), ("Dtto", 6, "Sonora")]


def test_parse_senators_sections_and_states():
    text = ("# Senadoras y Senadores electos por el principio de mayoría relativa (64)\n\n Sonora \n\n"
            "#### Sen. Heriberto Marcelo Aguilar Castillo\n\n Av. Paseo de la Reforma 135\n\n"
            "# Senadoras y Senadores electos por el principio de representacion proporcional (32)\n\n"
            "#### Sen. Ana Lilia Rivera Rivera\n")
    rows = parse_mx_senators(text)
    assert rows == [{"name": "Heriberto Marcelo Aguilar Castillo", "section": "relative_majority", "entity": "Sonora"},
                    {"name": "Ana Lilia Rivera Rivera", "section": "proportional_list", "entity": None}]


def test_customary_election_counts_as_elected():
    assert office_is_elected("customary_election") is True
