from polgraph.pipeline import Pipeline
from polgraph.world import EIU_URL, FH_URL, classify_world, resolve_iso2

from . import fakes

EIU_TEXT = """| Region | Rank | Country | Regime type | 2025 | 2024 |
| --- | --- | --- | --- | --- | --- |
| Europe | 1 | Norway | Full democracy | 9.81 | 9.81 |
| | 84 | Mexico | Hybrid regime | 5.40 | 5.32 |
| | 125 | Pakistan | Authoritarian | 2.44 | 2.84 |
| | 90 | Gambia | Hybrid regime | 4.47 | 4.4 |
| | 91 | The Gambia | Authoritarian | 1.00 | 1.0 |
"""
FH_TEXT = """#### 2024 countries (according to Freedom House)
| Country (2024) | | | | | | | |
| Kosovo | Yes | 3 | 4 | 60 | 26 | 34 | Partly free |
| Mexico | Yes | 3 | 4 | 59 | 26 | 33 | Partly free |
| Norway | Yes | 1 | 1 | 99 | 40 | 59 | Free |
| Serbia | Yes | 3 | 3 | 57 | 24 | 33 | Partly free |
"""


def test_classify_world(monkeypatch, settings, gateway, store, state):
    monkeypatch.setitem(fakes.PAGES, EIU_URL, EIU_TEXT)
    monkeypatch.setitem(fakes.PAGES, FH_URL, FH_TEXT)
    p = Pipeline(settings, gateway, store, state)
    p.run("MX", mode="research_only", stages=["democracy"])     # publisher-verified FH 2026 for Mexico
    res = classify_world(gateway, store)
    mx, no, pk, gm = (store.get_node(f"country:{c}") for c in ("MX", "NO", "PK", "GM"))
    assert (no["regime_group"], no["regime_class"]) == ("Democratic", "Full democracy")
    assert (mx["regime_group"], mx["regime_class"]) == ("Partly democratic", "Hybrid regime")
    assert pk["regime_group"] == "Authoritarian"
    assert gm["regime_class"] == "Hybrid regime"                      # first row per country wins
    assert store.get_node("country:FR")["regime_group"] == "Unclassified"
    assert "Kosovo" in res.unmatched["freedom_house"]                 # never mapped onto Serbia
    assert store.get_node("country:RS")["regime_by_source"]["freedom_house"]["class"] == "Partly free"
    # Publisher-verified Freedom House 2026 for Mexico is preferred over the 2024 compilation row.
    assert mx["regime_by_source"]["freedom_house"]["year"] == 2026
    assert mx["regime_by_source"]["freedom_house"]["verification"] == "verified"
    assert store.get_node("assessment:MX:freedom-house:2026")["verification"] == "verified"


def test_resolve_iso2_is_exact_only():
    assert resolve_iso2("Ivory Coast") == "CI" and resolve_iso2("Congo (Democratic Republic)") == "CD"
    assert resolve_iso2("Kosovo") is None and resolve_iso2("Somaliland") is None
    assert resolve_iso2("Narnia") is None
