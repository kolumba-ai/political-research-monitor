"""Country-level read API for the 3D explorer. Read-only views over the existing knowledge graph:
nothing here invents data. Unresearched countries return what the graph has (registry + regime class)."""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import Callable

from fastapi import APIRouter, HTTPException

from . import ids
from .config import REPO_ROOT
from .graph.store import GraphStore

POPULAR = {"direct_election", "customary_election"}
TTL_S = 120


def _country_row(n: dict, *, has_structure: bool) -> dict:
    return {"iso2": n.get("iso2"), "iso3": n.get("iso3"), "name": n.get("name"), "continent": n.get("continent"),
            "regime_group": n.get("regime_group") or "Unclassified", "regime_class": n.get("regime_class"),
            "regime_sources_agree": n.get("regime_sources_agree"),
            "research_status": n.get("research_status") or "not_researched",
            "has_structure": has_structure,
            "last_verified": n.get("last_verified")}


def _spec_status(iso2: str) -> dict | None:
    """The approved Structure Spec for this country (AGENTS.md), if one exists."""
    p = REPO_ROOT / "structures" / f"{iso2.upper()}.md"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    status = re.search(r"Status:\s*\*\*([^*]+)\*\*", text)
    version = re.search(r"\|\s*Version\s*\|\s*([^|\n]+)\|", text)
    official = re.search(r"\|\s*Official text\s*\|\s*(https?://\S+)", text)
    return {"path": f"structures/{iso2.upper()}.md", "status": status.group(1).strip() if status else None,
            "version": version.group(1).strip().strip("*") if version else None,
            "official_text_url": official.group(1) if official else None}


def country_stats(store: GraphStore, iso2: str) -> dict:
    """Counts for the country panel. Generic: works for any country and either store backend."""
    iso2 = iso2.upper()
    offices = [o for o in store.nodes_by_label("PoliticalOffice") if o["id"].startswith(f"office:{iso2}:")]
    office_ids = {o["id"]: o for o in offices}
    bodies = sum(1 for b in store.nodes_by_label("GovernmentBody") if b["id"].startswith(f"body:{iso2}:"))
    levels = [l for l in store.nodes_by_label("GovernmentLevel") if l["id"].startswith(f"level:{iso2}:")]
    holders, popular, evidence = set(), set(), Counter()
    offices_with_holder = set()
    for e in store.all_edges():
        if e["type"] != "HOLDS_OFFICE" or e["dst"] not in office_ids or not e.get("current"):
            continue
        holders.add(e["src"])
        offices_with_holder.add(e["dst"])
        evidence[e.get("evidence_status") or "unknown"] += 1
        if office_ids[e["dst"]].get("selection_method") in POPULAR:
            popular.add(e["src"])
    return {"levels": len(levels), "units_by_tier": dict(Counter(l.get("tier") or "unknown" for l in levels)),
            "bodies": bodies, "offices": len(offices),
            # AGENTS.md defines elected offices as those filled by voters. Legacy flags also
            # mark indirect elections as elected, so use the actual selection method here.
            "elected_offices": sum(1 for o in offices if o.get("selection_method") in POPULAR),
            "offices_with_current_holder": len(offices_with_holder), "current_officeholders": len(holders),
            "popularly_elected_officeholders": len(popular), "holdings_by_evidence": dict(evidence)}


def build_router(get_store: Callable[[], GraphStore], get_state: Callable) -> APIRouter:
    r = APIRouter()
    # Each application owns its cache. A module-global cache can serve another app's
    # graph and state when multiple app instances run in the same process.
    cache: dict[str, tuple[float, object]] = {}

    def cached(key: str, fn: Callable[[], object]):
        hit = cache.get(key)
        if hit and time.monotonic() - hit[0] < TTL_S:
            return hit[1]
        value = fn()
        cache[key] = (time.monotonic(), value)
        return value

    def country_rows():
        store = get_store()
        structured = {e["src"] for e in store.all_edges() if e["type"] == "HAS_GOVERNMENT_LEVEL"}
        return sorted((_country_row(n, has_structure=n["id"] in structured)
                       for n in store.nodes_by_label("Country")), key=lambda c: c["name"] or "")

    @r.get("/api/countries")
    def countries():
        return cached("countries", lambda: {"countries": country_rows()})

    @r.get("/api/world/overview")
    def overview():
        def build():
            rows = countries()["countries"]
            return {"countries": len(rows), "by_regime_group": dict(Counter(c["regime_group"] for c in rows)),
                    "researched": sorted(c["iso2"] for c in rows if c["has_structure"]),
                    "last_run": get_state().last_run()}
        return cached("overview", build)

    @r.get("/api/countries/{code}")
    def country(code: str):
        code = code.upper()

        def build():
            store = get_store()
            cid = ids.country_id(code)
            n = store.get_node(cid)
            if not n:
                raise HTTPException(404, f"unknown country code {code}")
            assessments = []
            for _, a in store.edges(cid, "HAS_ASSESSMENT", "out"):
                assessments.append({
                    "id": a["id"], "index": a.get("index"), "label": a.get("classification_raw"),
                    "classification": a.get("classification"), "year": a.get("assessment_year"),
                    "score": a.get("score"), "score_scale": a.get("score_scale"), "grey_zone": a.get("grey_zone"),
                    "verification": a.get("verification"), "evidence_url": a.get("evidence_url"),
                    "evidence_quote": a.get("evidence_quote"),
                    "sources": [{"url": s.get("url"), "title": s.get("name"), "domain": s.get("domain"),
                                 "retrieved_at": s.get("retrieved_at")} for s in store.sources_for(a["id"])]})
            assessments.sort(key=lambda a: (a["index"] or "", -(a["year"] or 0)))
            contact = None
            rule = store.get_node(ids.contact_rule_id(code))
            if rule:
                contact = {"id": rule["id"], "status": rule.get("status"), "review_status": rule.get("review_status"),
                           "status_reasons": rule.get("status_reasons"), "disclaimer": rule.get("disclaimer"),
                           "dimensions": rule.get("dimensions") or [], "laws": rule.get("laws") or [],
                           "last_verified": rule.get("last_verified"),
                           "sources": [{"url": s.get("url"), "title": s.get("name"), "domain": s.get("domain")}
                                       for s in store.sources_for(rule["id"])]}
            tiers = [{"id": l["id"], "name": l.get("name"), "tier": l.get("tier"), "article": l.get("article"),
                      "child_count": len(store.children(l["id"]))}
                     for _, l in store.edges(cid, "HAS_GOVERNMENT_LEVEL", "out")]
            tiers.sort(key=lambda t: {"national": 0, "state": 1}.get(t["tier"], 2))
            return {
                "country": {**_country_row(n, has_structure=bool(tiers)),
                            "m49": n.get("m49"), "un_region": n.get("un_region"),
                            "un_sub_region": n.get("un_sub_region"),
                            "system_of_government": n.get("system_of_government"),
                            "constitution_name": n.get("constitution_name"),
                            "constitution_url": n.get("constitution_url")},
                "regime": {"group": n.get("regime_group") or "Unclassified", "class": n.get("regime_class"),
                           "basis": n.get("regime_basis"), "sources_agree": n.get("regime_sources_agree"),
                           "by_source": n.get("regime_by_source") or {}},
                "assessments": assessments,
                "policy_checks": {"democracy": n.get("democracy_gate"), "contact": n.get("contact_gate"),
                                  "note": "Informational only; nothing is hidden or gated by these checks."},
                "contact_rules": contact,
                "structure": {"tiers": tiers, "spec": _spec_status(code),
                              "stats": country_stats(store, code) if tiers else None},
                "last_verified": n.get("last_verified"),
            }
        return cached(f"country:{code}", build)

    return r
