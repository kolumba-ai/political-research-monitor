"""Export / import the complete knowledge base as plain, diffable files.

Layout (see knowledge_base/README.md):
  graph/nodes.jsonl, graph/edges.jsonl   full graph, loadable into Neo4j or SQLite with `polgraph import`
  tables/*.csv                           flattened views for spreadsheets
  research/*                             provenance: Exa Agent outputs + citations, stage runs, claims, costs
  manifest.json                          counts, Exa spend, sha256 of every file

Deliberately NOT exported: raw third-party page text from the Exa response cache (copyrighted content,
~13 MB) and monitor webhook secrets. Short supporting snippets stay on SUPPORTED_BY edges.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from .graph.model import NODE_LABELS
from .graph.store import GraphStore
from .ids import slugify, utcnow
from .logging_setup import get_logger

log = get_logger("export")


def _dump_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(db: Path, sql: str) -> list[tuple]:
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def export_kb(store: GraphStore, data_dir: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("graph", "tables", "research", "research/agent_runs"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # ---------------- graph
    nodes = []
    for label in sorted(NODE_LABELS):
        for n in store.nodes_by_label(label):
            nodes.append({k: v for k, v in n.items() if k != "search"})
    nodes.sort(key=lambda n: n["id"])
    edges = store.all_edges()
    _dump_jsonl(out / "graph/nodes.jsonl", nodes)
    _dump_jsonl(out / "graph/edges.jsonl", edges)
    by_id = {n["id"]: n for n in nodes}

    # ---------------- tables: countries
    with (out / "tables/countries.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso2", "iso3", "name", "continent", "regime_group", "regime_class", "regime_basis",
                    "indices_agree", "eiu", "freedom_house", "vdem", "government_researched"])
        for n in nodes:
            if n["label"] != "Country":
                continue
            src = n.get("regime_by_source") or {}
            w.writerow([n.get("iso2"), n.get("iso3"), n.get("name"), n.get("continent"), n.get("regime_group"),
                        n.get("regime_class"), n.get("regime_basis"), n.get("regime_sources_agree"),
                        *[f"{src[k]['class']} ({src[k].get('year')})" if k in src else "" for k in
                          ("eiu", "freedom_house", "vdem")],
                        n.get("research_status") not in (None, "not_researched")])

    # ---------------- tables: officeholders (flattened path + sources)
    parent: dict[str, str] = {}
    for e in edges:
        if e["type"] in ("CONTAINS", "HAS_GOVERNMENT_LEVEL", "HAS_BODY", "HAS_OFFICE"):
            parent[e["dst"]] = e["src"]
    sources_of: dict[str, list[str]] = {}
    for e in edges:
        if e["type"] == "SUPPORTED_BY" and e["dst"] in by_id:
            sources_of.setdefault(e["src"], []).append(by_id[e["dst"]].get("url", ""))
    party_of: dict[str, str] = {}
    for e in edges:
        if e["type"] == "MEMBER_OF_PARTY" and e["dst"] in by_id:
            party_of[e["src"]] = by_id[e["dst"]].get("name", "")

    def path_names(nid: str) -> list[str]:
        out_, cur, guard = [], nid, 0
        while cur in parent and guard < 20:
            cur = parent[cur]
            out_.append(by_id.get(cur, {}).get("name", cur))
            guard += 1
        return list(reversed(out_))

    n_hold = 0
    with (out / "tables/officeholders.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["country", "path", "office", "selection_method", "is_elected", "seat_type", "constituency_code",
                    "person", "party", "current", "start", "end", "evidence_status", "confidence", "source_urls",
                    "office_id", "person_id"])
        for e in edges:
            if e["type"] != "HOLDS_OFFICE":
                continue
            person, office = by_id.get(e["src"], {}), by_id.get(e["dst"], {})
            p = path_names(e["dst"])
            w.writerow([p[2] if len(p) > 2 else "", " > ".join(p[3:]), office.get("name"), office.get("selection_method"),
                        office.get("is_elected"), e.get("seat_type") or office.get("seat_type"),
                        e.get("constituency_code"), person.get("name"), party_of.get(e["src"], ""), e.get("current"),
                        e.get("start"), e.get("end"), e.get("evidence_status"), e.get("confidence"),
                        "; ".join(sorted(set(sources_of.get(e.get("term_id", ""), [])))), e["dst"], e["src"]])
            n_hold += 1

    # ---------------- research provenance
    pipe_db, cache_db = data_dir / "pipeline.sqlite3", data_dir / "exa_cache.sqlite3"
    runs = [dict(zip(("run_id", "country", "mode", "started_at", "finished_at", "status", "summary"), r))
            for r in _rows(pipe_db, "SELECT run_id, country, mode, started_at, finished_at, status, summary "
                                    "FROM pipeline_runs ORDER BY started_at")]
    for r in runs:
        r["summary"] = json.loads(r["summary"]) if r["summary"] else None
    (out / "research/pipeline_runs.json").write_text(json.dumps(runs, indent=1, ensure_ascii=False, default=str))
    stages = [dict(zip(("country", "stage", "completed_at", "status", "summary"), r))
              for r in _rows(pipe_db, "SELECT country, stage, completed_at, status, summary FROM stage_runs "
                                      "ORDER BY country, stage")]
    for r in stages:
        r["summary"] = json.loads(r["summary"]) if r["summary"] else None
    (out / "research/stage_runs.json").write_text(json.dumps(stages, indent=1, ensure_ascii=False, default=str))
    claims = [dict(zip(("country", "stage", "subject", "claim", "status", "reason", "urls", "run_id", "created_at"), r))
              for r in _rows(pipe_db, "SELECT country, stage, subject, claim, status, reason, urls, run_id, created_at "
                                      "FROM claims ORDER BY id")]
    for c in claims:
        c["claim"], c["urls"] = json.loads(c["claim"]), json.loads(c["urls"])
    _dump_jsonl(out / "research/unresolved_claims.jsonl", claims)
    reviews = [dict(zip(("country", "check", "decision", "reviewer", "note", "created_at"), r))
               for r in _rows(pipe_db, "SELECT country, check_name, decision, reviewer, note, created_at FROM reviews")]
    (out / "research/reviews.json").write_text(json.dumps(reviews, indent=1, ensure_ascii=False))

    agent_files = 0
    for key, req, resp, created in _rows(cache_db, "SELECT key, request, response, created_at FROM cache "
                                                   "WHERE endpoint='agent' ORDER BY created_at"):
        meta, run = json.loads(req), json.loads(resp)
        output = run.get("output") or {}
        record = {"purpose": meta.get("purpose"), "query": meta.get("query"), "run_id": run.get("id"),
                  "status": run.get("status"), "stop_reason": run.get("stop_reason") or run.get("stopReason"),
                  "created_at": created, "cost_dollars": run.get("cost_dollars") or run.get("costDollars"),
                  "structured_output": output.get("structured"), "grounding": output.get("grounding"),
                  "text": output.get("text")}
        name = slugify(meta.get("purpose") or key)[:90] + f"-{key.split(':')[-1][:8]}.json"
        (out / "research/agent_runs" / name).write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str))
        agent_files += 1
    with (out / "research/exa_usage_ledger.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "endpoint", "purpose", "cost_usd", "estimated", "cached", "ref"])
        w.writerows(_rows(cache_db, "SELECT ts, endpoint, purpose, cost_usd, estimated, cached, ref FROM usage ORDER BY id"))
    spend = _rows(cache_db, "SELECT COALESCE(SUM(cost_usd),0) FROM usage")
    spend_total = round(float(spend[0][0]), 4) if spend else 0.0

    counts = store.counts()
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name not in ("manifest.json", "README.md"))
    manifest = {
        "exported_at": utcnow(), "format_version": 1,
        "counts": {"nodes": counts["nodes"], "edges": counts["edges"], "officeholdings": n_hold,
                   "agent_runs": agent_files, "unresolved_or_weak_claims": len(claims)},
        "exa_lifetime_spend_usd": spend_total,
        "pipeline_runs": [{k: r[k] for k in ("run_id", "country", "status", "finished_at")} for r in runs],
        "files": {str(p.relative_to(out)): {"bytes": p.stat().st_size, "sha256": _sha256(p)} for p in files},
        "not_included": ["raw page text from the Exa response cache (third-party copyrighted content)",
                         "monitor webhook secrets", "API keys / credentials"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    log.info("export.done", out=str(out), nodes=len(nodes), edges=len(edges), holdings=n_hold, agent_runs=agent_files)
    return manifest


def import_kb(store: GraphStore, src: Path) -> dict:
    """Load nodes then edges (idempotent upserts) into the configured store."""
    n = e = 0
    with (src / "graph/nodes.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            nid, label = row.pop("id"), row.pop("label")
            store.upsert_node(nid, label, row)
            n += 1
    with (src / "graph/edges.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            store.upsert_edge(row.pop("src"), row.pop("type"), row.pop("dst"), row)
            e += 1
    log.info("import.done", nodes=n, edges=e)
    return {"nodes": n, "edges": e}
