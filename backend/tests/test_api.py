import importlib

from fastapi.testclient import TestClient

from polgraph.pipeline import Pipeline


def _client(monkeypatch, settings, gateway, store, state):
    Pipeline(settings, gateway, store, state).run("MX", mode="research_only")
    api = importlib.import_module("polgraph.api")
    monkeypatch.setattr(api, "_store", lambda: store)
    monkeypatch.setattr(api, "_state", lambda: state)
    return TestClient(api.create_app())


def test_country_levels_always_visible_regardless_of_eligibility(monkeypatch, settings, gateway, store, state):
    c = _client(monkeypatch, settings, gateway, store, state)
    assert store.get_node("country:MX")["eligible"] is False
    res = c.get("/api/nodes/country:MX/children").json()
    assert res["gated"] is False
    assert "GovernmentLevel" in {ch["node"]["label"] for ch in res["children"]}


def test_office_children_show_verified_holders_and_detail_has_sources(monkeypatch, settings, gateway, store, state):
    c = _client(monkeypatch, settings, gateway, store, state)
    hit = c.get("/api/search?q=sheinbaum").json()["results"][0]
    assert hit["label"] == "Person" and hit["path"][0] == "earth"
    office_id = hit["path"][-2]
    kids = c.get(f"/api/nodes/{office_id}/children").json()["children"]
    assert [k["node"]["name"] for k in kids] == ["Claudia Sheinbaum Pardo"]
    d = c.get(f"/api/nodes/{hit['id']}").json()
    assert [p["label"] for p in d["path"]][:3] == ["Earth", "Continent", "Country"]
    assert {s["domain"] for s in d["sources"]} == {"gob.mx", "reuters.com"}


def test_single_source_holders_hidden_by_default(monkeypatch, settings, gateway, store, state):
    c = _client(monkeypatch, settings, gateway, store, state)
    office = next(n for n in store.nodes_by_label("PoliticalOffice") if n["name"] == "Governor of Jalisco")
    store.upsert_edge(store.search("Lemus", ["Person"])[0]["id"], "HOLDS_OFFICE", office["id"],
                      {"evidence_status": "single_source"})
    assert c.get(f"/api/nodes/{office['id']}/children").json()["children"] == []
    shown = c.get(f"/api/nodes/{office['id']}/children?include_single_source=true").json()["children"]
    assert len(shown) == 1


def test_webhook_rejects_unsigned(monkeypatch, settings, gateway, store, state):
    c = _client(monkeypatch, settings, gateway, store, state)
    assert c.post("/api/webhooks/exa", content=b"{}").status_code == 401
