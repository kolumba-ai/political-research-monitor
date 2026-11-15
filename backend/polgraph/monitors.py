"""Exa Monitors: detect election results, appointments and resignations, then revalidate.

Flow: monitor run (webhook or polled) -> event stored -> affected people/offices located in the graph
-> current holders re-verified against fresh Exa evidence -> affected stages marked stale so the
next `build` re-researches them. Monitor output is never written to the graph directly.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from . import ids
from .evidence import EvidenceStatus, mentions_any, mentions_name
from .exa_gateway import ExaGateway
from .graph.store import GraphStore
from .logging_setup import get_logger
from .registry import get_country
from .research import verify_holder
from .schemas import OfficeholderX
from .state import PipelineState

log = get_logger("monitors")

TOPICS = {
    "elections": "{country} election results officially declared winner governor mayor senator deputy",
    "appointments": "{country} government official sworn in OR appointed OR resigns OR removed from office",
    "judiciary": "{country} Supreme Court justice ministers appointed elected resigned",
}


def verify_signature(payload: bytes, header: str | None, secret: str, tolerance_s: int = 600,
                     now: float | None = None) -> bool:
    """Exa-Signature: t=<ts>,v1=<hex HMAC-SHA256 of '<ts>.<raw body>'>."""
    if not header or not secret:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = parts["t"]
        expected = hmac.new(secret.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, parts["v1"]):
            return False
        ts_val = float(ts) / (1000.0 if float(ts) > 1e12 else 1.0)
        return abs((now or time.time()) - ts_val) <= tolerance_s
    except (KeyError, ValueError):
        return False


def create_monitors(gw: ExaGateway, state: PipelineState, iso2: str, webhook_url: str, period: str = "1d") -> list[dict]:
    country = get_country(iso2)
    created = []
    for topic, tmpl in TOPICS.items():
        query = tmpl.format(country=country.name)
        resp = gw.client.monitors.create({
            "name": f"polgraph {country.iso2} {topic}",
            "search": {"query": query, "numResults": 10},
            "trigger": {"type": "interval", "period": period},
            "webhook": {"url": webhook_url, "events": ["monitor.run.completed"]},
            "metadata": {"app": "polgraph", "country": country.iso2, "topic": topic},
        })
        state.add_monitor(resp.id, country.iso2, topic, query, getattr(resp, "webhook_secret", None))
        created.append({"monitor_id": resp.id, "topic": topic, "query": query})
        log.info("monitor.created", monitor_id=resp.id, country=country.iso2, topic=topic)
    return created


def poll_runs(gw: ExaGateway, state: PipelineState) -> int:
    """Pull completed monitor runs (for local dev where Exa cannot reach a webhook)."""
    seen = {e["payload"].get("run_id") for e in state.events()}
    added = 0
    for m in state.monitors():
        resp = gw.client.monitors.runs.list(m["monitor_id"], limit=10)
        for run in resp.data:
            if run.id in seen or str(run.status) not in ("completed", "SearchMonitorRunStatus.completed"):
                continue
            results = (run.output.results if run.output else None) or []
            state.add_event(m["monitor_id"], {"run_id": run.id, "country": m["country"], "topic": m["topic"],
                                              "results": results, "source": "poll"})
            added += 1
    return added


def _event_results(payload: dict) -> list[dict]:
    data = payload.get("data") or payload
    out = (data.get("output") or {}).get("results") if isinstance(data.get("output"), dict) else None
    return out or payload.get("results") or []


def _stage_for_office(store: GraphStore, office_id: str) -> str:
    """office:<ISO>:<tier>:<...> -> pipeline stage that researches it."""
    parts = office_id.split(":")
    tier = parts[2] if len(parts) > 2 else ""
    if tier == "state":
        return "divisions"
    if tier == "municipal":
        container = store.get_node(f"level:{parts[1]}:municipal:{parts[3]}") if len(parts) > 3 else None
        return "special" if container and container.get("special_status") else "local"
    return "national"


def process_events(gw: ExaGateway, store: GraphStore, state: PipelineState, settings) -> list[dict]:
    reports = []
    for ev in state.events(status="pending"):
        payload = ev["payload"]
        mon = next((m for m in state.monitors() if m["monitor_id"] == ev["monitor_id"]), None)
        iso2 = payload.get("country") or (mon or {}).get("country")
        if not iso2:
            state.set_event_status(ev["id"], "ignored", "unknown country")
            continue
        results = _event_results(payload)
        text = " ".join(" ".join(filter(None, [r.get("title"), r.get("text"), " ".join(r.get("highlights") or [])]))
                        for r in results)
        touched_offices: dict[str, str] = {}
        for person in store.nodes_by_label("Person"):
            if person.get("country") != iso2.upper():
                continue
            if any(mentions_name(n, text) for n in [person["name"], *(person.get("aliases") or [])]):
                for e, office in store.edges(person["id"], "HOLDS_OFFICE", "out"):
                    if e.get("current"):
                        touched_offices[office["id"]] = person["id"]
        for office in store.nodes_by_label("PoliticalOffice"):
            if office["id"] in touched_offices or not office["id"].startswith(f"office:{iso2.upper()}:"):
                continue
            if office.get("single_seat") and mentions_any(office.get("keywords") or [], text) and \
                    mentions_any([office["name"].split(" of ")[-1]], text):
                cur = [p for e, p in store.edges(office["id"], "HOLDS_OFFICE", "in") if e.get("current")]
                touched_offices[office["id"]] = cur[0]["id"] if cur else ""
        rep = {"event": ev["id"], "offices": [], "stale_stages": set()}
        country = get_country(iso2)
        for oid, pid in touched_offices.items():
            office = store.get_node(oid)
            store.upsert_node(oid, "PoliticalOffice", {"needs_revalidation": True,
                                                       "revalidation_trigger": [r.get("url") for r in results][:5]})
            stage = _stage_for_office(store, oid)
            rep["stale_stages"].add(stage)
            if pid:
                person = store.get_node(pid)
                ver = verify_holder(gw, settings, country, OfficeholderX(full_name=person["name"]),
                                    [office["name"], office.get("title_local")], office.get("keywords") or [],
                                    None, [], purpose=f"revalidate:{oid}")
                store.upsert_edge(pid, "HOLDS_OFFICE", oid, {"evidence_status": ver.status.value,
                                                             "last_verified": ids.utcnow(),
                                                             "revalidated_after_event": ev["id"]})
                if ver.status == EvidenceStatus.unsupported:
                    store.upsert_edge(pid, "HOLDS_OFFICE", oid, {"status": "disputed"})
                rep["offices"].append({"office": oid, "holder": pid, "revalidation": ver.status.value})
            else:
                rep["offices"].append({"office": oid, "holder": None, "revalidation": "no current holder"})
        for st in rep["stale_stages"]:
            state.mark_stage(iso2.upper(), st, "stale", {"reason": f"monitor event {ev['id']}"})
        rep["stale_stages"] = sorted(rep["stale_stages"])
        state.set_event_status(ev["id"], "processed", f"{len(touched_offices)} offices flagged")
        reports.append(rep)
        log.info("monitor.event_processed", event=ev["id"], offices=len(touched_offices), stale=rep["stale_stages"])
    return reports
