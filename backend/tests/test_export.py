import csv
import json

from polgraph.export import export_kb, import_kb
from polgraph.graph.store import SqliteGraphStore
from polgraph.pipeline import Pipeline


def test_export_import_round_trip(settings, gateway, store, state, tmp_path):
    Pipeline(settings, gateway, store, state).run("MX", mode="research_only")
    out = tmp_path / "kb"
    # pipeline state lives in the `state` fixture's file; point the exporter at its directory
    state_dir = tmp_path
    (state_dir / "pipeline.sqlite3").write_bytes((tmp_path / "p.sqlite3").read_bytes())
    m = export_kb(store, state_dir, out)
    assert m["counts"]["officeholdings"] == 2
    assert (out / "graph/nodes.jsonl").exists() and (out / "tables/countries.csv").exists()
    rows = list(csv.DictReader((out / "tables/officeholders.csv").open()))
    she = next(r for r in rows if r["person"] == "Claudia Sheinbaum Pardo")
    assert she["country"] == "Mexico" and "gob.mx" in she["source_urls"] and she["evidence_status"] == "verified"
    assert any(json.loads(l)["subject"].startswith("Ficticia") for l in (out / "research/unresolved_claims.jsonl").open())
    assert list((out / "research/agent_runs").glob("*.json"))
    assert all(f in m["files"] for f in ("graph/nodes.jsonl", "graph/edges.jsonl"))

    fresh = SqliteGraphStore(tmp_path / "fresh.sqlite3")
    res = import_kb(fresh, out)
    assert fresh.counts() == store.counts() and res["nodes"] == sum(store.counts()["nodes"].values())
    p = store.search("Sheinbaum", ["Person"])[0]["id"]
    assert [n["id"] for n in fresh.path_from_root(p)] == [n["id"] for n in store.path_from_root(p)]
    assert fresh.get_node(p)["first_seen"] == store.get_node(p)["first_seen"]
