"""End-to-end pipeline behaviour against the fake Exa client."""
from polgraph.pipeline import Pipeline


def _pipe(settings, gateway, store, state):
    return Pipeline(settings, gateway, store, state)


def test_seed_world_from_iso_registry(settings, gateway, store, state):
    n = _pipe(settings, gateway, store, state).seed_world()
    assert n == 249
    mx = store.path_from_root("country:MX")
    assert [x["name"] for x in mx] == ["Earth", "North America", "Mexico"]
    assert [x["name"] for x in store.path_from_root("country:BR")][1] == "South America"


def test_production_mode_halts_when_gates_do_not_pass(settings, gateway, store, state, fake_exa):
    summary = _pipe(settings, gateway, store, state).run("MX", mode="production")
    assert summary["gates"]["eligible"] is False
    assert "halted" in summary
    # disagreement (Partly Free vs electoral democracy, grey zone) -> needs review, never silently eligible
    assert summary["gates"]["democracy"]["status"] == "needs_review"
    assert summary["gates"]["contact"]["status"] == "needs_review"   # awaiting human review
    assert not store.edges("country:MX", "HAS_GOVERNMENT_LEVEL", "out")
    assert not any(c == ("agent.create", "national") for c in fake_exa.calls)


def test_research_only_builds_verified_path_with_sources(settings, gateway, store, state):
    p = _pipe(settings, gateway, store, state)
    summary = p.run("MX", mode="research_only")
    assert summary["gates"]["eligible"] is False                       # still not eligible
    persons = {n["name"]: n for n in store.nodes_by_label("Person")}
    assert set(persons) == {"Claudia Sheinbaum Pardo", "Pablo Lemus Navarro"}  # fabricated holders rejected
    path = store.path_from_root(persons["Claudia Sheinbaum Pardo"]["id"])
    assert [n["label"] for n in path] == ["Earth", "Continent", "Country", "GovernmentLevel", "GovernmentBody",
                                          "PoliticalOffice", "Person"]
    e, office = store.edges(persons["Claudia Sheinbaum Pardo"]["id"], "HOLDS_OFFICE", "out")[0]
    assert e["evidence_status"] == "verified" and e["pipeline_mode"] == "research_only"
    assert office["is_elected"] is True and office["selection_method"] == "direct_election"
    srcs = store.sources_for(e["term_id"])
    assert {s["domain"] for s in srcs} == {"gob.mx", "reuters.com"}
    unresolved = {c["subject"].split(" @ ")[0]: c for c in state.claims("MX")}
    assert unresolved["Atlantis"]["status"] == "unmatched"
    assert unresolved["Ficticia Inventada Ruiz"]["status"] == "unsupported"
    party = store.edges(persons["Pablo Lemus Navarro"]["id"], "MEMBER_OF_PARTY", "out")[0][1]
    assert party["id"] == "party:MX:mc"
    assert summary["coverage"]["stages"]


def test_incremental_refresh_skips_fresh_stages(settings, gateway, store, state, fake_exa):
    p = _pipe(settings, gateway, store, state)
    p.run("MX", mode="research_only", stages=["democracy", "contact", "national"])
    n_agent = len(fake_exa.agent.runs.created)
    s2 = p.run("MX", mode="research_only", stages=["democracy", "contact", "national"])
    assert len(fake_exa.agent.runs.created) == n_agent
    assert all(v == {"skipped": "fresh"} for v in s2["stages"].values())
    state.mark_stage("MX", "national", "stale", {})
    s3 = p.run("MX", mode="research_only", stages=["national"])
    assert "skipped" not in s3["stages"]["national"]


def test_human_review_unlocks_production(settings, gateway, store, state):
    from polgraph.registry import get_country
    p = _pipe(settings, gateway, store, state)
    p.run("MX", mode="production", stages=["democracy", "contact"])
    state.add_review("MX", "democracy", "approve", "tester", "reviewed sources")
    state.add_review("MX", "contact", "approve", "tester", "reviewed statutes")
    gates = p.evaluate_gates(get_country("MX"))
    assert gates["eligible"] is True


def test_legislators_stage_separates_seat_types(settings, gateway, store, state, fake_exa):
    p = _pipe(settings, gateway, store, state)
    p.run("MX", mode="research_only", stages=["divisions"])
    s = p.run("MX", mode="research_only", stages=["legislators"], divisions=["MX-JAL"])
    leg = s["stages"]["legislators"]
    # JD-1..JD-20 fits in one 50-constituency slice, plus one reserved-seat slice
    assert leg["assemblies"]["MX-JAL"]["slices"] == 2
    offices = {n["name"]: n for n in store.nodes_by_label("PoliticalOffice") if "Congress of Jalisco" in n["name"]}
    gen = offices["Member of the Congress of Jalisco (general seat)"]
    women = offices["Member of the Congress of Jalisco (reserved seat for women)"]
    assert gen["is_elected"] is True and gen["selection_method"] == "direct_election" and gen["seats"] == 20
    assert women["selection_method"] == "indirect_election"
    holders = {p["name"]: e for e, p in store.edges(gen["id"], "HOLDS_OFFICE", "in")}
    assert set(holders) == {"Ana López Pérez"} and holders["Ana López Pérez"]["constituency_code"] == "JD-5"
    assert {p["name"] for _, p in store.edges(women["id"], "HOLDS_OFFICE", "in")} == {"María Torres Díaz"}
    assert "Invented Member Name" not in {n["name"] for n in store.nodes_by_label("Person")}
    assert any(c["subject"].startswith("Invented Member Name") for c in state.claims("MX"))
