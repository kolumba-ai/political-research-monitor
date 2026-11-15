"""FastAPI service: lazy graph expansion, inspection, search, reports and the Exa webhook."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import REPO_ROOT, get_settings
from .evidence import DISPLAY_AS_VERIFIED, EvidenceStatus
from .graph.store import GraphStore, open_store
from .logging_setup import configure_logging, get_logger
from .monitors import verify_signature
from .state import PipelineState

log = get_logger("api")
FRONTEND = REPO_ROOT / "frontend"
VERIFIED = {s.value for s in DISPLAY_AS_VERIFIED}


@lru_cache
def _store() -> GraphStore:
    return open_store(get_settings())


@lru_cache
def _state() -> PipelineState:
    return PipelineState(get_settings().data_dir / "pipeline.sqlite3")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(title="Political Knowledge Graph", version="0.1.0")

    def node_summary(n: dict, store: GraphStore) -> dict:
        kids = store.children(n["id"])
        return {"id": n["id"], "label": n["label"], "name": n.get("name"), "child_count": len(kids),
                "eligible": n.get("eligible"), "research_status": n.get("research_status"),
                "evidence_status": n.get("evidence_status"), "classification": n.get("classification"),
                "status": n.get("status"), "selection_method": n.get("selection_method"),
                "is_elected": n.get("is_elected"), "template": n.get("template"),
                "regime_group": n.get("regime_group"), "regime_class": n.get("regime_class"),
                "last_verified": n.get("last_verified"), "image_url": n.get("image_url")}

    def holder_visible(edge: dict, include_single: bool, include_former: bool) -> bool:
        if not include_former and not edge.get("current"):
            return False
        st = edge.get("evidence_status")
        return st in VERIFIED or (include_single and st == EvidenceStatus.single_source.value)

    @app.get("/api/meta")
    def meta():
        store = _store()
        return {"backend": s.graph_backend, "counts": store.counts(), "last_run": _state().last_run()}

    @app.get("/api/nodes/{node_id:path}/children")
    def children(node_id: str, research_only: bool = False, include_single_source: bool = False,
                 include_former: bool = False):
        store = _store()
        node = store.get_node(node_id)
        if not node:
            raise HTTPException(404, "node not found")
        gated = False
        out = []
        for ch in store.children(node_id):
            e, n = ch["edge"], ch["node"]
            if e["type"] in ("HOLDS_OFFICE", "CONTESTS_OFFICE") and not holder_visible(e, include_single_source,
                                                                                     include_former):
                continue
            out.append({"edge": {k: e.get(k) for k in ("type", "direction", "current", "status", "evidence_status",
                                                       "confidence", "start", "end", "constituency_code",
                                                       "seat_type")},
                        "node": node_summary(n, store)})
        # Constitutional order for government tiers (federal level first), then seat numbers, then names.
        tier_rank = {"national": 0, "state": 1, "state_unit": 2, "municipal": 3, "special_local": 3}

        def _order(c):
            code = c["edge"].get("constituency_code") or ""
            num = int(code.rsplit("-", 1)[-1]) if code.rsplit("-", 1)[-1].isdigit() else 10 ** 6
            tier = tier_rank.get(store.get_node(c["node"]["id"]).get("tier"), 9) \
                if c["node"]["label"] == "GovernmentLevel" and c["node"]["id"].count(":") <= 2 else 9
            return (c["node"]["label"], tier, num, c["node"]["name"] or "")
        out.sort(key=_order)
        return {"node": node_summary(node, store), "children": out, "gated": gated,
                "gate_message": ("Country has not passed the eligibility gates; enable research-only mode to view "
                                 "its government structure.") if gated else None}

    @app.get("/api/nodes/{node_id:path}")
    def node_detail(node_id: str):
        store = _store()
        node = store.get_node(node_id)
        if not node:
            raise HTTPException(404, "node not found")
        sources = [{**src, "edge": e} for e, src in store.edges(node_id, "SUPPORTED_BY", "out")]
        extra = {}
        if node["label"] == "Person":
            extra["offices"] = [{"edge": e, "office": {"id": o["id"], "name": o.get("name")}}
                                for e, o in store.edges(node_id, "HOLDS_OFFICE", "out")]
            extra["parties"] = [{"edge": e, "party": {"id": p["id"], "name": p.get("name"), "acronym": p.get("acronym")}}
                                for e, p in store.edges(node_id, "MEMBER_OF_PARTY", "out")]
            terms = [t for _, t in store.edges(node_id, "TERM_HELD_BY", "in")]
            extra["terms"] = [{**t, "sources": store.sources_for(t["id"])} for t in terms]
            for t in terms:
                for e, src in store.edges(t["id"], "SUPPORTED_BY", "out"):
                    sources.append({**src, "edge": e, "via_term": t["id"]})
        if node["label"] == "PoliticalOffice":
            extra["holders"] = [{"edge": e, "person": {"id": p["id"], "name": p.get("name")}}
                                for e, p in store.edges(node_id, "HOLDS_OFFICE", "in")]
        path = [{"id": p["id"], "label": p["label"], "name": p.get("name")} for p in store.path_from_root(node_id)]
        return {"node": node, "sources": sources, "path": path, **extra}

    from .world_api import build_router
    app.include_router(build_router(_store, _state))
    from .claims_api import build_router as build_claims_router
    from .metrics_api import MetricsExport, build_router as build_metrics_router
    metrics_exports: dict = {}

    def claim_metrics(code: str, claim: dict) -> dict | None:
        root = Path(s.claims_export_dir)
        return metrics_exports.setdefault(root, MetricsExport(root)).for_post(
            code.upper(), claim.get("politician_id"), claim.get("oriane_content_id"), claim.get("platform"),
            claim.get("platform_id"))
    app.include_router(build_claims_router(lambda: s.claims_export_dir, lambda pid: _store().get_node(pid),
                                           claim_metrics))
    app.include_router(build_metrics_router(lambda: s.claims_export_dir, lambda pid: _store().get_node(pid),
                                            metrics_exports))
    from .pipeline_api import build_router as build_pipeline_router
    app.include_router(build_pipeline_router(lambda: s.pipeline_database_url, lambda: s.claims_export_dir))

    @app.get("/api/search")
    def search(q: str, limit: int = 25):
        store = _store()
        res = store.search(q, None, limit)
        return {"results": [{**node_summary(n, store),
                             "path": [p["id"] for p in store.path_from_root(n["id"])]} for n in res]}

    @app.get("/api/report")
    def report(country: str = "MX"):
        from .exa_gateway import ExaGateway
        st = _state()
        claims = st.claims(country.upper())
        last = st.last_run(country.upper())
        gw = ExaGateway(s)
        return {"last_run": last, "stages": st.stages(country.upper()),
                "unresolved_claims": [c for c in claims if c["status"] not in ("verified",)][-200:],
                "usage": gw.usage_report()}

    @app.post("/api/webhooks/exa")
    async def exa_webhook(request: Request):
        body = await request.body()
        sig = request.headers.get("Exa-Signature")
        st = _state()
        match = next((m for m in st.monitors() if m["webhook_secret"] and verify_signature(body, sig, m["webhook_secret"])), None)
        if not match:
            log.warning("webhook.rejected", reason="bad signature")
            raise HTTPException(401, "invalid signature")
        payload = json.loads(body or b"{}")
        payload.setdefault("country", match["country"])
        eid = st.add_event(match["monitor_id"], payload)
        log.info("webhook.accepted", monitor=match["monitor_id"], event=eid)
        return JSONResponse({"ok": True, "event": eid})

    explorer = REPO_ROOT / "frontend-3d" / "dist"
    if explorer.exists():
        # Built 3D explorer (npm run build) served at /explorer; `npm run dev` proxies /api in development.
        app.mount("/explorer", StaticFiles(directory=explorer, html=True), name="explorer")

    if FRONTEND.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

        @app.get("/")
        def index():
            return FileResponse(Path(FRONTEND) / "index.html")

    return app


app = create_app()
