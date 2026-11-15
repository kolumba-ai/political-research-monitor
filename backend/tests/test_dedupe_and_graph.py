import pytest

from polgraph import ids
from polgraph.evidence import EvidenceStatus, SupportingSource, Verification
from polgraph.graph.dedupe import party_key, same_person, title_similarity
from polgraph.graph.writer import GraphWriter
from polgraph.schemas import OfficeholderX


@pytest.mark.parametrize("a,b,expected", [
    ("Claudia Sheinbaum", "Claudia Sheinbaum Pardo", True),
    ("CLAUDIA SHEINBAUM PARDO", "claudia sheinbaum pardo", True),
    ("Dra. Claudia Sheinbaum", "Claudia Sheinbaum Pardo", True),
    ("Clara Brugada Molina", "Clara Marina Brugada Molina", True),
    ("Samuel García Sepúlveda", "Samuel Garcia Sepulveda", True),
    ("Juan Pérez López", "Juan García López", False),   # shared maternal surname only
    ("María López", "José López", False),
    ("Sheinbaum", "Claudia Sheinbaum", False),            # single token is never enough
])
def test_same_person(a, b, expected):
    assert same_person(a, b) is expected


def test_party_key_prefers_acronym():
    assert party_key("Movimiento Regeneración Nacional", "MORENA") == party_key("Morena", None) == "morena"
    assert party_key("Movimiento Ciudadano (MC)", None) == "mc"
    assert party_key(None, None) is None


def test_ids_are_canonical():
    assert ids.person_id("mx", "Dra. Claudia Sheinbaum Pardo") == ids.person_id("MX", "claudia sheinbaum pardo")
    assert ids.source_id("https://www.gob.mx/a/?utm_source=x#frag") == ids.source_id("https://www.gob.mx/a")
    assert ids.registrable_domain("https://www.jalisco.gob.mx/x") == "jalisco.gob.mx"
    assert ids.registrable_domain("https://es.wikipedia.org/wiki/X") == "wikipedia.org"
    assert title_similarity("President of Mexico", "President of the Mexico") == 1.0


def _setup(store):
    w = GraphWriter(store, "research_only", "run-test")
    w.node("earth", "Earth", name="Earth")
    w.node("continent:north-america", "Continent", name="North America")
    w.edge("earth", "CONTAINS", "continent:north-america")
    w.node("country:MX", "Country", name="Mexico")
    w.edge("continent:north-america", "CONTAINS", "country:MX")
    lid = ids.level_id("MX", "national")
    w.node(lid, "GovernmentLevel", name="National")
    w.edge("country:MX", "HAS_GOVERNMENT_LEVEL", lid)
    bid = ids.body_id(lid, "Presidency")
    w.node(bid, "GovernmentBody", name="Presidency")
    w.edge(lid, "HAS_BODY", bid)
    return w, bid


def _ver(*urls):
    return Verification(EvidenceStatus.verified if len(urls) > 1 else EvidenceStatus.official_single,
                        [SupportingSource(u, None, ids.registrable_domain(u), 1, "agent_citation") for u in urls])


def test_upserts_are_idempotent_and_merge(store):
    store.upsert_node("country:MX", "Country", {"name": "Mexico", "iso2": "MX"})
    store.upsert_node("country:MX", "Country", {"eligible": False, "iso2": None})
    n = store.get_node("country:MX")
    assert n["name"] == "Mexico" and n["iso2"] == "MX" and n["eligible"] is False
    assert store.counts()["nodes"] == {"Country": 1}


def test_edge_requires_existing_endpoints(store):
    store.upsert_node("earth", "Earth", {"name": "Earth"})
    with pytest.raises(KeyError):
        store.upsert_edge("earth", "CONTAINS", "continent:nowhere")
    with pytest.raises(ValueError):
        store.upsert_edge("earth", "LIKES", "earth")


def test_person_and_office_dedup_and_path(store):
    w, bid = _setup(store)
    o1 = w.office(bid, title_en="President of Mexico", title_local="Presidente de los Estados Unidos Mexicanos",
                  selection_method="direct_election", is_elected=True)
    o2 = w.office(bid, title_en="President of the Mexico", title_local=None, selection_method="direct_election",
                  is_elected=True)
    assert o1 == o2
    r1 = w.holding("MX", o1, OfficeholderX(full_name="Claudia Sheinbaum", term_start="2024-10-01", party_acronym="Morena"),
                   _ver("https://www.gob.mx/presidencia", "https://reuters.com/x"), single_seat=True)
    r2 = w.holding("MX", o1, OfficeholderX(full_name="Claudia Sheinbaum Pardo", term_start="2024-10-01",
                                          party_name="Movimiento Regeneración Nacional", party_acronym="MORENA"),
                   _ver("https://www.gob.mx/presidencia"), single_seat=True)
    assert r1["person"] == r2["person"] and r1["term"] == r2["term"] and r1["party"] == r2["party"]
    assert len(store.nodes_by_label("Person")) == 1 and len(store.nodes_by_label("PoliticalParty")) == 1
    person = store.get_node(r1["person"])
    assert person["name"] == "Claudia Sheinbaum Pardo" and "Claudia Sheinbaum" in person["aliases"]
    path = [n["label"] for n in store.path_from_root(r1["person"])]
    assert path == ["Earth", "Continent", "Country", "GovernmentLevel", "GovernmentBody", "PoliticalOffice", "Person"]
    term_sources = store.sources_for(r1["term"])
    assert {s["domain"] for s in term_sources} == {"gob.mx", "reuters.com"}


def test_new_verified_holder_demotes_previous_to_former(store):
    w, bid = _setup(store)
    o = w.office(bid, title_en="President of Mexico", title_local=None, selection_method="direct_election", is_elected=True)
    old = w.holding("MX", o, OfficeholderX(full_name="Andrés Manuel López Obrador", term_start="2018-12-01"),
                    _ver("https://www.gob.mx/a", "https://reuters.com/b"), single_seat=True)
    new = w.holding("MX", o, OfficeholderX(full_name="Claudia Sheinbaum Pardo", term_start="2024-10-01"),
                    _ver("https://www.gob.mx/c", "https://reuters.com/d"), single_seat=True)
    assert new["demoted"] == [old["person"]]
    edges = {p["id"]: e for e, p in store.edges(o, "HOLDS_OFFICE", "in")}
    assert edges[old["person"]]["current"] is False and edges[old["person"]]["status"] == "former"
    assert edges[new["person"]]["current"] is True


def test_unsupported_claims_are_never_persisted(store):
    w, bid = _setup(store)
    o = w.office(bid, title_en="President of Mexico", title_local=None, selection_method="direct_election", is_elected=True)
    with pytest.raises(ValueError):
        w.holding("MX", o, OfficeholderX(full_name="Made Up Person"), Verification(EvidenceStatus.unsupported),
                  single_seat=True)
    assert store.nodes_by_label("Person") == []


def test_children_rules(store):
    w, bid = _setup(store)
    kids = store.children("country:MX")
    assert [k["node"]["label"] for k in kids] == ["GovernmentLevel"]
    assert store.search("mexi", ["Country"])[0]["id"] == "country:MX"


@pytest.mark.parametrize("a,b,expected", [
    ("Muhammad Shehbaz Sharif", "Shehbaz Sharif", True),
    ("Syed Murad Ali Shah", "Murad Ali Shah", True),
    ("Mian Muhammad Nawaz Sharif", "Nawaz Sharif", True),
    ("Muhammad Ali", "Ali Khan", False),            # never strip down to a single token
    ("Shehbaz Sharif", "Nawaz Sharif", False),
])
def test_same_person_with_leading_prefixes(a, b, expected):
    assert same_person(a, b) is expected


def test_pakistan_subdivision_aliases():
    from polgraph.registry import match_subdivision
    for name, code in [("KPK", "PK-KP"), ("Islamabad Capital Territory", "PK-IS"), ("AJK", "PK-JK"),
                       ("Punjab Province", "PK-PB"), ("Gilgit-Baltistan", "PK-GB"), ("Sindh", "PK-SD")]:
        assert match_subdivision("PK", name)["code"] == code


def test_offices_with_different_qualifiers_never_merge(store):
    w, bid = _setup(store)
    a = w.office(bid, title_en="Member of the Provincial Assembly of Balochistan (reserved seat for women)",
                 title_local=None, selection_method="indirect_election", is_elected=True, seat_type="women_reserved", seats=11)
    b = w.office(bid, title_en="Member of the Provincial Assembly of Balochistan (reserved seat for minorities)",
                 title_local=None, selection_method="indirect_election", is_elected=True, seat_type="minority_reserved", seats=3)
    assert a != b and store.get_node(a)["seats"] == 11 and store.get_node(b)["seats"] == 3


@pytest.mark.parametrize("raw,clean", [
    ("Mr. Abdul Majeed Badini", "Abdul Majeed Badini"), ("Maj (retired) Bashir Ahmed Barech", "Bashir Ahmed Barech"),
    ("Barrister Murtaza Wahab Siddiqui", "Murtaza Wahab Siddiqui"), ("Dra. Claudia Sheinbaum", "Claudia Sheinbaum"),
    ("Mian Aslam Iqbal", "Mian Aslam Iqbal"), ("Dr Ali", "Dr Ali"), ("Drake Ramos Ortiz", "Drake Ramos Ortiz"),
])
def test_clean_display_name(raw, clean):
    from polgraph.ids import clean_display_name
    assert clean_display_name(raw) == clean


def test_person_display_name_is_cleaned_and_merged(store):
    w, _ = _setup(store)
    a = w.person("PK", "Mr. Abdul Majeed Badini")
    b = w.person("PK", "Abdul Majeed Badini")
    assert a == b and store.get_node(a)["name"] == "Abdul Majeed Badini"
