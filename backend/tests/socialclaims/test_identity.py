import json
from pathlib import Path

import pytest

from socialclaims import accounts, identity
from socialclaims.query import QueryTerms, build_query, query_id
from socialclaims.systema import AmbiguousName, KnowledgeBaseGraph, list_persons, read_person, resolve_name

from .conftest import kb_graph, write_kb

PLATFORMS = ["tiktok", "instagram"]
REPO_KB = Path(__file__).resolve().parents[3] / "knowledge_base"


def test_read_person_snapshot(graph):
    s = read_person(graph, "person:MX:claudia-sheinbaum-pardo")
    assert s.country_code == "MX" and s.pipeline_mode == "research_only"
    assert s.graph_run == "run-MX-20261114T230338Z"
    assert s.person_updated_at == "2026-11-14T23:07:40+00:00"
    [o] = s.current_offices
    assert o.office_id == "office:MX:national:executive:president" and o.term_id == "term:t1"
    assert o.level_tier == "national" and o.body_name == "Executive" and o.evidence_status == "verified"
    assert s.parties[0]["party_id"] == "party:MX:morena" and s.parties[0]["coalition"] == "Sigamos Haciendo Historia"
    assert s.tracking_status == "trackable"


def test_eligibility_filter(graph):
    status = {s.politician_id: s.tracking_status for s in list_persons(graph, "MX", include_ineligible=True)}
    assert status == {
        "person:MX:claudia-sheinbaum-pardo": "trackable",
        "person:MX:clara-marina-brugada-molina": "trackable",       # official_single
        "person:MX:alfredo-ramirez-bedolla": "needs_review",        # single_source
        "person:MX:former-official": "ineligible",                  # no current holding
    }
    assert {s.politician_id for s in list_persons(graph, "MX")} == set(status) - {"person:MX:former-official"}
    assert read_person(graph, "person:MX:nobody") is None


def test_production_country_mode(tmp_path):
    nodes, edges = kb_graph()
    nodes[0].update(eligible=True, research_status="enriched")
    for e in edges:
        if e["type"] == "HOLDS_OFFICE":
            e["pipeline_mode"] = "production"
    g = KnowledgeBaseGraph(write_kb(tmp_path / "kb2", nodes, edges))
    assert read_person(g, "person:MX:claudia-sheinbaum-pardo").pipeline_mode == "production"


def test_resolve_name_never_creates_identities(graph):
    assert resolve_name(graph, "Clara Brugada") == "person:MX:clara-marina-brugada-molina"
    assert resolve_name(graph, "claudia sheinbaum") == "person:MX:claudia-sheinbaum-pardo"
    with pytest.raises(AmbiguousName):
        resolve_name(graph, "Unknown Person")


@pytest.mark.skipif(not (REPO_KB / "graph" / "nodes.jsonl").exists(), reason="knowledge_base export not present")
def test_reads_committed_knowledge_base():
    g = KnowledgeBaseGraph(REPO_KB)
    s = read_person(g, "person:MX:claudia-sheinbaum-pardo")
    assert s.tracking_status == "trackable" and s.pipeline_mode == "research_only"
    assert s.graph_run and s.current_offices


def test_query_branches_and_stable_hash(graph):
    s = read_person(graph, "person:MX:clara-marina-brugada-molina")
    q = build_query(s, QueryTerms(), PLATFORMS)
    assert [b["name"] for b in q["branches"]] == ["spoken", "written", "onscreen"]   # no verified handles yet
    values = q["branches"][1]["filters"]["caption"]["includesExactly"]["values"]
    assert "Clara Marina Brugada Molina" in values and "Clara Brugada" in values and "Brugada" not in values
    terms = QueryTerms(handles={"tiktok": ["@ClaraBrugada"]}, profile_ids=["prf_x"], hashtags=["ClaraBrugada"],
                       extra_variants=["Brugada"])
    q2 = build_query(s, terms, list(reversed(PLATFORMS)))
    assert [b["name"] for b in q2["branches"]] == ["own", "coauthored", "tagged", "spoken", "written", "onscreen", "hashtag"]
    assert q2["branches"][0]["filters"] == {"profileId": {"includes": ["prf_x"]},
                                            "profileHandle": {"exactMatch": {"values": ["clarabrugada"]}}}
    assert q2["branches"][-1]["filters"]["hashtags"]["exactMatch"]["values"] == ["#clarabrugada"]
    assert query_id(q2) == query_id(json.loads(json.dumps(q2)))
    assert query_id(q2) == query_id(build_query(s, terms, PLATFORMS))     # platform order does not matter
    assert query_id(q2) != query_id(q)
    assert len(query_id(q)) == 64


def test_sync_versions_and_reconciliation(conn, kb_dir, graph):
    pid = "person:MX:claudia-sheinbaum-pardo"
    r1 = identity.sync_ref(conn, graph, pid, PLATFORMS)
    assert r1["status"] == "created" and r1["version"] == 1
    assert identity.sync_ref(conn, graph, pid, PLATFORMS)["status"] == "unchanged"

    nodes, edges = kb_graph()
    for n in nodes:
        if n["id"] == pid:
            n.update(aliases=["Dra Sheinbaum"], updated_at="2026-12-01T00:00:00+00:00")
    g2 = KnowledgeBaseGraph(write_kb(kb_dir, nodes, edges))
    r2 = identity.sync_ref(conn, g2, pid, PLATFORMS)
    assert r2["status"] == "new_version" and r2["version"] == 2 and r2["query_id"] != r1["query_id"]
    rows = conn.execute("SELECT version, superseded_at FROM politician_ref WHERE politician_id=%s ORDER BY version",
                        (pid,)).fetchall()
    assert rows[0]["superseded_at"] is not None and rows[1]["superseded_at"] is None
    job = conn.execute("SELECT * FROM reconciliation_job WHERE politician_id=%s", (pid,)).fetchone()
    assert job["kind"] == "identity_changed" and set(job["diff"]) == {"aliases"}
    assert (job["from_version"], job["to_version"]) == (1, 2)


def test_review_queue_and_missing(conn, graph):
    r = identity.sync_ref(conn, graph, "person:MX:alfredo-ramirez-bedolla", PLATFORMS)
    assert r["tracking_status"] == "needs_review"
    assert conn.execute("SELECT count(*) c FROM review WHERE item_type='politician'").fetchone()["c"] == 1
    m = identity.sync_ref(conn, graph, "person:MX:merged-away", PLATFORMS)
    assert m["status"] == "missing_in_system_a"
    assert conn.execute("SELECT kind FROM reconciliation_job").fetchone()["kind"] == "id_missing"


def test_redirects(conn, graph):
    identity.add_redirect(conn, "person:MX:claudia-sheinbaum", "person:MX:claudia-sheinbaum-pardo", "merge", "tester")
    r = identity.sync_ref(conn, graph, "person:MX:claudia-sheinbaum", PLATFORMS)
    assert r["politician_id"] == "person:MX:claudia-sheinbaum-pardo" and r["status"] == "created"
    with pytest.raises(ValueError):
        identity.add_redirect(conn, "person:MX:claudia-sheinbaum-pardo", "person:MX:claudia-sheinbaum", "x", "t")


def test_accounts_need_human_approval_and_change_query(conn, graph):
    pid = "person:MX:clara-marina-brugada-molina"
    v1 = identity.sync_ref(conn, graph, pid, PLATFORMS)
    with pytest.raises(ValueError):
        accounts.approve(conn, pid, "tiktok", "@clarabrugada", "reviewer", [])
    with pytest.raises(ValueError):
        accounts.approve(conn, pid, "tiktok", "@clarabrugada", "", ["https://cdmx.gob.mx"])
    accounts.approve(conn, pid, "tiktok", "@ClaraBrugada", "reviewer", ["https://jefaturadegobierno.cdmx.gob.mx"],
                     oriane_profile_id="prf_clara")
    terms = identity.load_terms(conn, pid)
    assert terms.handles == {"tiktok": ["clarabrugada"]} and terms.profile_ids == ["prf_clara"]
    v2 = identity.sync_ref(conn, graph, pid, PLATFORMS)
    assert v2["version"] == 2 and v2["query_id"] != v1["query_id"]
    # query-only changes do not open identity reconciliation jobs
    assert conn.execute("SELECT count(*) c FROM reconciliation_job").fetchone()["c"] == 0


def test_discover_only_proposes(conn, graph):
    import httpx
    from socialclaims.oriane import OrianeClient

    def handler(req):
        return httpx.Response(200, json={"metadata": {"requestId": "req_p"}, "data": {"results": [
            {"id": "prf_a", "platform": "tiktok", "handle": "ClaraBrugadaM", "displayName": "Clara Brugada",
             "followersCount": 900000, "isVerified": True}]}})
    client = OrianeClient("k", "https://connect.test", transport=httpx.MockTransport(handler))
    snap = read_person(graph, "person:MX:clara-marina-brugada-molina")
    out = accounts.discover(conn, client, snap, PLATFORMS)
    assert out[0]["handle"] == "clarabrugadam"
    row = conn.execute("SELECT status, oriane_profile_id FROM politician_account").fetchone()
    assert row == {"status": "proposed", "oriane_profile_id": "prf_a"}
    assert identity.load_terms(conn, snap.politician_id).handles == {}
    assert conn.execute("SELECT credits FROM oriane_usage").fetchone()["credits"] == 3
