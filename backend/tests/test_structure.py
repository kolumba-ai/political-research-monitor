from polgraph import ids
from polgraph.graph.writer import GraphWriter
from polgraph.structure import apply_spec_mx, restructure_local


def _old_shape(store):
    w = GraphWriter(store, "t", "t")
    w.node("earth", "Earth", name="Earth")
    w.node("country:MX", "Country", name="Mexico")
    w.edge("earth", "CONTAINS", "country:MX")
    for tier, name in (("national", "National / Federal government"), ("state", "State / Provincial government"),
                       ("municipal", "Municipal / Local government")):
        w.node(ids.level_id("MX", tier), "GovernmentLevel", name=name, tier=tier)
        w.edge("country:MX", "HAS_GOVERNMENT_LEVEL", ids.level_id("MX", tier))
    for code, name in (("MX-JAL", "Jalisco"), ("MX-CMX", "Ciudad de México")):
        sid = ids.level_id("MX", "state", code)
        w.node(sid, "GovernmentLevel", name=name, tier="state_unit", iso_3166_2=code)
        w.edge(ids.level_id("MX", "state"), "CONTAINS", sid)
    mun = ids.level_id("MX", "municipal")
    gdl = ids.level_id("MX", "municipal", "MX-JAL", "Guadalajara")
    w.node(gdl, "GovernmentLevel", name="Guadalajara, Jalisco", tier="municipal_unit", parent_iso_3166_2="MX-JAL")
    w.edge(mun, "CONTAINS", gdl)
    cdmx = ids.level_id("MX", "municipal", "MX-CMX")
    w.node(cdmx, "GovernmentLevel", name="Mexico City — Borough", tier="special_local")
    w.edge(mun, "CONTAINS", cdmx)
    tmpl = ids.body_id(mun, "standard-council")
    w.node(tmpl, "GovernmentBody", name="Municipal council — standard structure", template=True)
    w.edge(mun, "HAS_BODY", tmpl)
    o = w.office(tmpl, title_en="Municipal President", title_local=None, selection_method="direct_election",
                 is_elected=True)
    return gdl, cdmx, o


def test_local_government_nests_under_its_state(store):
    gdl, cdmx, tmpl_office = _old_shape(store)
    stats = restructure_local(store, "MX")
    assert stats["units_moved"] == 1 and stats["special_moved"] == 1 and stats["country_tier_removed"]
    assert store.get_node(ids.level_id("MX", "municipal")) is None and store.get_node(tmpl_office) is None
    path = [n["name"] for n in store.path_from_root(gdl)]
    assert path == ["Earth", "Mexico", "State / Provincial government", "Jalisco",
                    "Municipalities of Jalisco (Municipios)", "Guadalajara, Jalisco"]
    assert [n["name"] for n in store.path_from_root(cdmx)][-2:] == [
        "Ciudad de México", "Alcaldías of Ciudad de México (Demarcaciones territoriales)"]
    assert restructure_local(store, "MX")["units_moved"] == 0          # idempotent


def test_mx_spec_federal_judiciary(store):
    _old_shape(store)
    w = GraphWriter(store, "t", "t")
    nat = ids.level_id("MX", "national")
    old = ids.body_id(nat, "Federal circuit courts and district courts")
    w.node(old, "GovernmentBody", name="Federal circuit courts and district courts", branch="judicial")
    w.edge(nat, "HAS_BODY", old)
    cm = w.office(old, title_en="Circuit magistrate", title_local=None, selection_method="direct_election", is_elected=True)
    dj = w.office(old, title_en="District judge", title_local=None, selection_method="direct_election", is_elected=True)
    stats = apply_spec_mx(store)
    assert stats["offices_moved"] == 2 and stats["bodies_removed"] == 1
    bodies = {b["name"]: b["id"] for _, b in store.edges(nat, "HAS_BODY", "out")}
    for name in ("Regional Plenaries (Plenos Regionales)", "Collegiate Circuit Courts (Tribunales Colegiados de Circuito)",
                 "Collegiate Appeals Courts (Tribunales Colegiados de Apelación)", "District Courts (Juzgados de Distrito)"):
        assert name in bodies
    assert store.parent(cm)["name"].startswith("Collegiate Circuit Courts")
    assert store.parent(dj)["name"].startswith("District Courts")
    assert store.get_node(nat)["name"] == "Federation (Federación)"
    assert store.get_node(ids.level_id("MX", "state"))["name"] == "Federal entities (Entidades federativas)"
    assert apply_spec_mx(store)["offices_moved"] == 0                   # idempotent
