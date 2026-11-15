import json

from polgraph.bundle import build_bundle, export_bundles
from socialclaims import identity
from socialclaims.systema import BundleDir, list_persons, read_person

from .conftest import kb_graph, write_kb

PID = "person:MX:claudia-sheinbaum-pardo"


def test_bundle_holds_country_to_politician_path_and_party_leaf(graph):
    b = build_bundle(graph, "country:MX", PID)
    assert b["paths"]["office:MX:national:executive:president"] == [
        "country:MX", "level:MX:national", "body:MX:national:executive", "office:MX:national:executive:president"]
    types = {(e["src"], e["type"], e["dst"]) for e in b["edges"]}
    assert (PID, "MEMBER_OF_PARTY", "party:MX:morena") in types
    assert ("term:t1", "TERM_HELD_BY", PID) in types and ("term:t1", "SUPPORTED_BY", "source:gob") in types
    assert {n["label"] for n in b["nodes"]} >= {"Country", "GovernmentLevel", "GovernmentBody", "PoliticalOffice",
                                                "Term", "Person", "PoliticalParty", "Source"}
    assert build_bundle(graph, "country:MX", PID)["sha256"] == b["sha256"]


def test_snapshot_comes_from_bundle_and_records_its_hash(graph):
    snap = read_person(graph, PID)
    assert snap.tracking_status == "trackable" and snap.input_errors == []
    assert snap.bundle_sha256 == build_bundle(graph, "country:MX", PID)["sha256"]
    assert snap.offices[0].level_id == "level:MX:national"
    assert read_person(graph, "person:MX:clara-marina-brugada-molina").input_warnings == ["no MEMBER_OF_PARTY leaf"]


def test_missing_path_or_wrong_country_is_invalid_input(tmp_path):
    nodes, edges = kb_graph()
    edges = [e for e in edges if e["type"] != "HAS_GOVERNMENT_LEVEL"]
    from socialclaims.systema import KnowledgeBaseGraph
    kb = KnowledgeBaseGraph(write_kb(tmp_path / "kb", nodes, edges))
    snap = read_person(kb, PID)
    assert snap.tracking_status == "invalid_input"
    assert "no Country -> ... -> PoliticalOffice path" in snap.input_errors[0]
    assert read_person(kb, PID, "PK") is None


def test_tampered_bundle_is_rejected(graph, tmp_path):
    export_bundles(graph, tmp_path / "bundles", ["MX"])
    path = tmp_path / "bundles" / "MX" / "person__MX__claudia-sheinbaum-pardo.json"
    b = json.loads(path.read_text())
    next(n for n in b["nodes"] if n["id"] == PID)["name"] = "Someone Else"
    path.write_text(json.dumps(b))
    assert "bundle sha256 does not match its content" in read_person(BundleDir(tmp_path / "bundles"), PID).input_errors


def test_exported_bundles_feed_system_b(graph, conn, tmp_path):
    summary = export_bundles(graph, tmp_path / "bundles", ["MX"])
    assert summary == {"MX": 4}
    bd = BundleDir(tmp_path / "bundles")
    from_files = {s.politician_id: s.tracking_status for s in list_persons(bd, "MX")}
    from_graph = {s.politician_id: s.tracking_status for s in list_persons(graph, "MX")}
    assert from_files == from_graph
    r = identity.sync_ref(conn, bd, PID, ["tiktok", "instagram"])
    ref = identity.current_ref(conn, PID)
    stored = conn.execute("SELECT country_id, bundle FROM system_a_bundle WHERE sha256 = %s",
                          (ref["bundle_sha256"],)).fetchone()
    assert r["status"] == "created" and stored["country_id"] == "country:MX"
    assert stored["bundle"]["politician"] == PID


def test_bundle_that_loses_its_path_creates_a_non_trackable_version(conn, tmp_path):
    from socialclaims.systema import KnowledgeBaseGraph
    nodes, edges = kb_graph()
    good = KnowledgeBaseGraph(write_kb(tmp_path / "a", nodes, edges))
    assert identity.sync_ref(conn, good, PID, ["tiktok"])["tracking_status"] == "trackable"
    broken = KnowledgeBaseGraph(write_kb(tmp_path / "b", nodes, [e for e in edges if e["type"] != "HAS_GOVERNMENT_LEVEL"]))
    r = identity.sync_ref(conn, broken, PID, ["tiktok"])
    assert (r["status"], r["tracking_status"]) == ("new_version", "invalid_input")
