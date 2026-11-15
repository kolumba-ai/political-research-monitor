"""Command line entry point: `python -m polgraph <command>`."""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .logging_setup import configure_logging


def _components():
    from .exa_gateway import ExaGateway
    from .graph.store import open_store
    from .pipeline import Pipeline
    from .state import PipelineState
    s = get_settings()
    gw = ExaGateway(s)
    store = open_store(s)
    state = PipelineState(s.data_dir / "pipeline.sqlite3")
    return s, gw, store, state, Pipeline(s, gw, store, state)


def cmd_build(a) -> int:
    s, gw, store, state, pipe = _components()
    if a.budget:
        s.exa_run_budget_usd = a.budget
    stages = a.stages.split(",") if a.stages else None
    summary = pipe.run(a.country, mode="production" if a.production else "research_only", stages=stages,
                       force=a.force or a.no_cache, no_cache=a.no_cache,
                       divisions=a.divisions.split(",") if a.divisions else None)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if "error" not in summary else 2


def cmd_classify_world(a) -> int:
    from .world import classify_world
    _, gw, store, _, pipe = _components()
    pipe.seed_world()
    res = classify_world(gw, store, ttl_hours=0 if a.no_cache else None)
    print(json.dumps({"matched_rows": res.matched, "unmatched_rows": res.unmatched, "countries_by_group": res.groups,
                      "usage": gw.usage_report()["this_run"]}, indent=2))
    return 0


def cmd_export(a) -> int:
    from pathlib import Path
    from .export import export_kb
    s, _, store, _, _ = _components()
    m = export_kb(store, s.data_dir, Path(a.out).resolve())
    print(json.dumps({"out": str(Path(a.out).resolve()), **m["counts"], "exa_spend_usd": m["exa_lifetime_spend_usd"]},
                     indent=2))
    return 0


def cmd_export_bundles(a) -> int:
    from pathlib import Path
    from .bundle import JsonlGraph, export_bundles
    g = JsonlGraph(Path(a.kb).resolve()) if a.kb else _components()[2]
    print(json.dumps(export_bundles(g, Path(a.out).resolve(), a.country), indent=2))
    return 0


def cmd_import(a) -> int:
    from pathlib import Path
    from .export import import_kb
    _, _, store, _, _ = _components()
    print(json.dumps(import_kb(store, Path(a.src).resolve()), indent=2))
    return 0


def cmd_repair_people(a) -> int:
    from .repair import repair_people
    _, _, store, _, _ = _components()
    print(json.dumps(repair_people(store, a.country), indent=2))
    return 0


def cmd_apply_structure(a) -> int:
    from .structure import apply_spec_mx, restructure_local
    _, _, store, _, _ = _components()
    iso = a.country.upper()
    out = {"restructure_local": restructure_local(store, iso)}
    if iso == "MX":
        out["spec"] = apply_spec_mx(store)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_seed(a) -> int:
    _, _, _, _, pipe = _components()
    print(f"seeded {pipe.seed_world()} countries under Earth")
    return 0


def cmd_gates(a) -> int:
    from .registry import get_country
    _, _, _, _, pipe = _components()
    print(json.dumps(pipe.evaluate_gates(get_country(a.country)), indent=2))
    return 0


def cmd_review(a) -> int:
    from .registry import get_country
    _, _, _, state, pipe = _components()
    state.add_review(a.country.upper(), a.check, a.decision, a.reviewer, a.note or "")
    print(json.dumps(pipe.evaluate_gates(get_country(a.country)), indent=2))
    return 0


def cmd_report(a) -> int:
    s, gw, store, state, pipe = _components()
    iso = a.country.upper()
    cov = pipe.coverage(iso)
    claims = state.claims(iso)
    rep = {"country": iso, "last_run": state.last_run(iso), "coverage": cov,
           "unresolved_claims": [{k: c[k] for k in ("stage", "subject", "status", "reason")} for c in claims],
           "api_usage": gw.usage_report()}
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    lr = rep["last_run"] or {}
    print(f"== Report for {iso} (last run {lr.get('run_id')} mode={lr.get('mode')} status={lr.get('status')})")
    country = store.get_node(f"country:{iso}") or {}
    for g in ("democracy_gate", "contact_gate"):
        gate = country.get(g) or {}
        print(f"  {g}: {gate.get('status')} — " + "; ".join(gate.get("reasons", [])[:4]))
    print(f"  eligible for production: {country.get('eligible')}")
    print(f"  nodes: {cov['nodes_by_label']}")
    print(f"  offices: {cov['offices']} (elected {cov['elected_offices']}), with current holder: "
          f"{cov['offices_with_current_holder']}")
    print(f"  current holders by evidence: {cov['current_holders_by_evidence']}")
    print(f"  unresolved/weak claims: {len(claims)}")
    for c in claims[:25]:
        print(f"    [{c['stage']}] {c['status']}: {c['subject'][:90]} — {c['reason'][:120]}")
    u = rep["api_usage"]
    print(f"  Exa usage: lifetime ${u['lifetime_cost_usd']}")
    for e in u["by_endpoint"]:
        print(f"    {e['endpoint']}: {e['calls']} calls ({e['cache_hits']} cache hits) ${e['cost_usd']}")
    return 0


def cmd_path(a) -> int:
    _, _, store, _, _ = _components()
    hits = store.search(a.query, ["Person"], 5)
    if not hits:
        print("no matching person")
        return 1
    p = hits[0]
    print(" -> ".join(f"{n['label']}:{n.get('name')}" for n in store.path_from_root(p["id"])))
    for e, office in store.edges(p["id"], "HOLDS_OFFICE", "out"):
        print(f"\n{p['name']} HOLDS_OFFICE {office['name']} [current={e.get('current')}, "
              f"evidence={e.get('evidence_status')}, confidence={e.get('confidence')}, since={e.get('start')}]")
        term = store.get_node(e.get("term_id")) if e.get("term_id") else None
        for se, src in store.edges(term["id"], "SUPPORTED_BY", "out") if term else []:
            print(f"   source (tier {src.get('authority_tier')}, via {se.get('via')}): {src.get('url')}")
            if se.get("snippet"):
                print(f"      {se['snippet'][:220]}")
    return 0


def cmd_portraits(a) -> int:
    from .portraits import find_portraits
    s, gw, store, _, _ = _components()
    if a.budget:
        s.exa_run_budget_usd = a.budget
    res = find_portraits(gw, store, a.country, limit=a.limit, force=a.force, ids=a.ids or None, workers=a.workers)
    print(json.dumps({**res, "usage": gw.usage_report()["this_run"]}, indent=2, default=str))
    return 0


def cmd_serve(a) -> int:
    import uvicorn
    s = get_settings()
    uvicorn.run("polgraph.api:app", host=a.host or s.api_host, port=a.port or s.api_port, reload=False)
    return 0


def cmd_monitors(a) -> int:
    from .monitors import create_monitors, poll_runs, process_events
    s, gw, store, state, _ = _components()
    if a.action == "create":
        if not a.webhook_url or not a.webhook_url.startswith("https://"):
            print("--webhook-url must be a public https URL pointing at /api/webhooks/exa", file=sys.stderr)
            return 2
        print(json.dumps(create_monitors(gw, state, a.country, a.webhook_url, a.period), indent=2))
    elif a.action == "poll":
        print(f"stored {poll_runs(gw, state)} new monitor run events")
    elif a.action == "process":
        print(json.dumps(process_events(gw, store, state, s), indent=2, default=str))
    elif a.action == "list":
        print(json.dumps([{k: v for k, v in m.items() if k != "webhook_secret"} for m in state.monitors()], indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    p = argparse.ArgumentParser(prog="polgraph", description="Exa-powered political knowledge graph")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="research a country and build/refresh its graph")
    b.add_argument("--country", default="MX")
    b.add_argument("--research-only", action="store_true", help="(default; kept for compatibility)")
    b.add_argument("--production", action="store_true",
                   help="stop after the eligibility checks unless both pass (by default every country is enriched; "
                        "the checks are recorded as information)")
    b.add_argument("--stages", help="comma list: democracy,contact,national,divisions,local,special,legislators "
                                    "(legislators and directories are opt-in: individual legislature members; official rosters)")
    b.add_argument("--divisions", help="limit the legislators stage to ISO 3166-2 codes, e.g. PK-BA,PK-PB")
    b.add_argument("--force", action="store_true", help="re-run stages even if fresh (re-uses cached Exa responses)")
    b.add_argument("--no-cache", action="store_true", help="re-run stages AND bypass cached Exa responses (costs credits)")
    b.add_argument("--budget", type=float, help="override EXA_RUN_BUDGET_USD for this run")
    b.set_defaults(fn=cmd_build)

    sub.add_parser("seed", help="seed Earth/continents/countries from ISO 3166 + UN M49").set_defaults(fn=cmd_seed)

    ap = sub.add_parser("apply-structure", help="apply the approved Structure Spec (structures/<ISO>.md): nest local "
                                                 "government under divisions, constitutional names and bodies")
    ap.add_argument("--country", required=True)
    ap.set_defaults(fn=cmd_apply_structure)

    rp = sub.add_parser("repair-people", help="split Person nodes that were merged by name matching in error")
    rp.add_argument("--country")
    rp.set_defaults(fn=cmd_repair_people)

    ex = sub.add_parser("export", help="export the complete knowledge base to plain files (for the repo)")
    ex.add_argument("--out", default="../knowledge_base")
    ex.set_defaults(fn=cmd_export)
    eb = sub.add_parser("export-bundles", help="write one System B input bundle per politician "
                        "(country + politician + connecting subgraph)")
    eb.add_argument("--country", nargs="+", required=True)
    eb.add_argument("--out", default="../knowledge_base/bundles")
    eb.add_argument("--kb", help="read an exported knowledge_base dir instead of the configured graph store")
    eb.set_defaults(fn=cmd_export_bundles)
    im = sub.add_parser("import", help="load an exported knowledge base into the configured graph store")
    im.add_argument("--from", dest="src", default="../knowledge_base")
    im.set_defaults(fn=cmd_import)

    cw = sub.add_parser("classify-world", help="regime classification (Democratic / Partly democratic / Authoritarian) "
                                               "for every country, from index tables fetched via Exa Contents")
    cw.add_argument("--no-cache", action="store_true")
    cw.set_defaults(fn=cmd_classify_world)

    g = sub.add_parser("gates", help="re-evaluate eligibility gates from stored evidence")
    g.add_argument("--country", default="MX")
    g.set_defaults(fn=cmd_gates)

    r = sub.add_parser("review", help="record a human review decision for a gate")
    r.add_argument("--country", default="MX")
    r.add_argument("--check", choices=["democracy", "contact"], required=True)
    r.add_argument("--decision", choices=["approve", "reject"], required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--note")
    r.set_defaults(fn=cmd_review)

    rep = sub.add_parser("report", help="coverage, unresolved claims, API usage")
    rep.add_argument("--country", default="MX")
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(fn=cmd_report)

    pa = sub.add_parser("path", help="print Earth -> ... -> person path with sources")
    pa.add_argument("query")
    pa.set_defaults(fn=cmd_path)

    pt = sub.add_parser("portraits", help="find a verified portrait for each politician with Exa (image_url on Person)")
    pt.add_argument("--country", default="MX")
    pt.add_argument("--limit", type=int, help="at most N people this run (executives first, then legislators)")
    pt.add_argument("--force", action="store_true", help="re-check people that already have a result")
    pt.add_argument("--budget", type=float, help="USD ceiling for this run")
    pt.add_argument("--workers", type=int, default=1,
                    help="people searched in parallel (Exa calls stay within EXA_REQUESTS_PER_SECOND)")
    pt.add_argument("ids", nargs="*", help="only these person ids")
    pt.set_defaults(fn=cmd_portraits)

    sv = sub.add_parser("serve", help="run the API + web UI")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.set_defaults(fn=cmd_serve)

    m = sub.add_parser("monitors", help="Exa monitors for change detection")
    m.add_argument("action", choices=["create", "poll", "process", "list"])
    m.add_argument("--country", default="MX")
    m.add_argument("--webhook-url")
    m.add_argument("--period", default="1d")
    m.set_defaults(fn=cmd_monitors)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
