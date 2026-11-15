"""Read-only explorer views, including cache isolation and constitutional metadata."""
import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polgraph.graph.writer import GraphWriter
from polgraph.world_api import build_router, country_stats


def _client(store, state):
    app = FastAPI()
    app.include_router(build_router(lambda: store, lambda: state))
    return TestClient(app)


def _country(store, code="MX", name="Mexico", **props):
    store.upsert_node(f"country:{code}", "Country", {"iso2": code, "name": name, **props})


def _level(store, code="MX"):
    node_id = f"level:{code}:national"
    store.upsert_node(node_id, "GovernmentLevel", {
        "name": "Federation", "tier": "national", "article": "Constitution art. 49"})
    store.upsert_edge(f"country:{code}", "HAS_GOVERNMENT_LEVEL", node_id)
    return node_id


def test_structure_availability_comes_from_graph_not_research_status(store, state):
    _country(store, research_status="research_only_enriched", regime_group="Partly democratic")
    _level(store)
    # A prior eligibility-only run can mark research complete without creating any tiers.
    _country(store, "FR", "France", research_status="enriched")
    # Conversely, a manually loaded structure can exist before research_status is set.
    _country(store, "US", "United States", research_status="not_researched")
    _level(store, "US")
    client = _client(store, state)

    rows = client.get("/api/countries").json()["countries"]
    assert [row["name"] for row in rows] == ["France", "Mexico", "United States"]
    assert {row["iso2"]: row["has_structure"] for row in rows} == {"FR": False, "MX": True, "US": True}
    overview = client.get("/api/world/overview").json()
    assert overview["countries"] == 3
    assert overview["researched"] == ["MX", "US"]
    assert overview["by_regime_group"] == {"Partly democratic": 1, "Unclassified": 2}
    assert overview["last_run"] is None
    detail = client.get("/api/countries/FR").json()
    assert detail["country"]["has_structure"] is False
    assert detail["structure"]["tiers"] == []
    assert detail["structure"]["stats"] is None


def test_country_details_include_ungated_structure_and_attributed_evidence(store, state):
    _country(store, eligible=False, research_status="research_only_enriched",
             democracy_gate={"passed": False}, contact_gate={"passed": False})
    level = _level(store)
    writer = GraphWriter(store, "test", "test")
    for year, verification in [(2024, "secondary_compilation"), (2026, "verified")]:
        assessment = f"assessment:MX:freedom-house:{year}"
        writer.node(assessment, "DemocracyAssessment", index="freedom_house", assessment_year=year,
                    classification_raw="Partly Free", classification="partly_free", score=58,
                    score_scale="0–100", verification=verification,
                    evidence_quote="Stored evidence excerpt", evidence_url="https://official.example/rating")
        writer.edge("country:MX", "HAS_ASSESSMENT", assessment)
        writer.support(assessment, ["https://official.example/rating"])
    writer.node("contactrule:MX", "ContactRule", status="restricted", review_status="pending_human_review",
                dimensions=[{"channel": "email", "status": "restricted"}], laws=[{"name": "Example statute"}])
    writer.edge("country:MX", "HAS_CONTACT_RULE", "contactrule:MX")
    writer.support("contactrule:MX", ["https://official.example/statute"])
    client = _client(store, state)

    response = client.get("/api/countries/mx")
    assert response.status_code == 200
    detail = response.json()
    assert detail["country"]["has_structure"] is True
    assert detail["structure"]["tiers"] == [{"id": level, "name": "Federation", "tier": "national",
                                             "article": "Constitution art. 49", "child_count": 0}]
    assert detail["structure"]["stats"]["levels"] == 1
    assert detail["policy_checks"]["democracy"] == {"passed": False}
    assert [assessment["year"] for assessment in detail["assessments"]] == [2026, 2024]
    evidence = detail["assessments"][0]
    assert evidence["verification"] == "verified"
    assert evidence["evidence_quote"] == "Stored evidence excerpt"
    assert evidence["sources"][0]["url"] == "https://official.example/rating"
    assert detail["contact_rules"]["status"] == "restricted"
    assert detail["contact_rules"]["review_status"] == "pending_human_review"
    assert detail["contact_rules"]["sources"][0]["url"] == "https://official.example/statute"


def test_world_cache_is_scoped_to_each_application(store, state):
    _country(store)
    first = _client(store, state)
    assert first.get("/api/countries/MX").json()["country"]["name"] == "Mexico"
    assert first.get("/api/countries").json()["countries"][0]["name"] == "Mexico"
    assert first.get("/api/world/overview").json()["countries"] == 1

    _country(store, name="Updated Mexico")
    _country(store, "FR", "France")
    second = _client(store, state)
    assert second.get("/api/countries/MX").json()["country"]["name"] == "Updated Mexico"
    assert len(second.get("/api/countries").json()["countries"]) == 2
    assert second.get("/api/world/overview").json()["countries"] == 2


def test_expired_cache_picks_up_graph_updates(monkeypatch, store, state):
    _country(store)
    client = _client(store, state)
    assert client.get("/api/countries/MX").json()["country"]["has_structure"] is False
    assert client.get("/api/world/overview").json()["researched"] == []
    _level(store)
    # Force the normal expiration path without waiting two minutes in a unit test.
    monkeypatch.setattr("polgraph.world_api.TTL_S", 0)
    assert client.get("/api/countries/MX").json()["country"]["has_structure"] is True
    assert client.get("/api/world/overview").json()["researched"] == ["MX"]


def test_stats_distinguish_people_offices_and_election_methods(store, state):
    _country(store)
    _level(store)
    writer = GraphWriter(store, "test", "test")
    for key, method in [("direct", "direct_election"), ("customary", "customary_election"),
                        ("indirect", "indirect_election"), ("appointed", "appointment")]:
        writer.node(f"office:MX:{key}", "PoliticalOffice", name=key, selection_method=method,
                    # Legacy flags can wrongly mark indirect election as popular election.
                    is_elected=method != "appointment")
    writer.node("body:MX:example", "GovernmentBody", name="Example body")
    writer.node("person:MX:one", "Person", name="Example Person")
    writer.node("person:MX:former", "Person", name="Former Person")
    writer.node("person:MX:two", "Person", name="Other Person")
    for office in ["direct", "indirect"]:
        writer.edge("person:MX:one", "HOLDS_OFFICE", f"office:MX:{office}", current=True,
                    evidence_status="verified_multi_source")
    writer.edge("person:MX:two", "HOLDS_OFFICE", "office:MX:customary", current=True,
                evidence_status="single_source")
    writer.edge("person:MX:former", "HOLDS_OFFICE", "office:MX:direct", current=False,
                evidence_status="verified_multi_source")
    # Other countries must not leak into Mexico's totals.
    writer.node("office:PK:direct", "PoliticalOffice", name="Other office", selection_method="direct_election",
                is_elected=True)
    writer.edge("person:MX:two", "HOLDS_OFFICE", "office:PK:direct", current=True)

    stats = country_stats(store, "mx")
    assert stats["bodies"] == 1 and stats["offices"] == 4
    assert stats["elected_offices"] == 2
    assert stats["current_officeholders"] == 2
    assert stats["popularly_elected_officeholders"] == 2
    assert stats["offices_with_current_holder"] == 3
    assert stats["holdings_by_evidence"] == {"verified_multi_source": 2, "single_source": 1}


def test_world_routes_are_mounted_and_unknown_country_is_404(monkeypatch, store, state):
    _country(store)
    api = importlib.import_module("polgraph.api")
    monkeypatch.setattr(api, "_store", lambda: store)
    monkeypatch.setattr(api, "_state", lambda: state)
    client = TestClient(api.create_app())
    assert client.get("/api/countries").status_code == 200
    assert client.get("/api/world/overview").status_code == 200
    assert client.get("/api/countries/MX").status_code == 200
    assert client.get("/api/countries/ZZ").status_code == 404


def test_spec_metadata_comes_from_local_document(monkeypatch, store, state, tmp_path):
    _country(store)
    specs = tmp_path / "structures"
    specs.mkdir()
    (specs / "MX.md").write_text(
        "Status: **AWAITING REVIEW**\n\n"
        "| Version | **Example consolidated version** |\n"
        "| Official text | https://official.example/constitution.pdf (official text) |\n",
        encoding="utf-8")
    monkeypatch.setattr("polgraph.world_api.REPO_ROOT", tmp_path)
    detail = _client(store, state).get("/api/countries/MX").json()
    assert detail["structure"]["spec"] == {
        "path": "structures/MX.md", "status": "AWAITING REVIEW", "version": "Example consolidated version",
        "official_text_url": "https://official.example/constitution.pdf"}
    # A draft document is metadata, never evidence that a graph structure exists.
    assert detail["country"]["has_structure"] is False
