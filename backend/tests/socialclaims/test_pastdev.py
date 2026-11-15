"""past.dev claim export: trace-root paging, truncated-text recall fallback, identity mapping, and the read API."""
from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from polgraph.claims_api import build_router
from socialclaims.pastdev import PastDevClient, parse_payload, post_url, pull


def _content(cid: str, claims: list[dict], *, person="person:MX:clara-brugada-molina", platform="tiktok",
             published="2026-11-10T22:08:38.000Z", pad: str = "") -> str:
    payload = {"politician": {"name": "Clara Brugada", "system_a_person_id": person, "country": "MX"},
               "source": {"oriane_content_id": cid, "platform": platform, "platform_id": "77", "uploader_handle": "h",
                          "published_at": published, "caption": "caption" + pad},
               "engagement_snapshot": {"views": 100, "likes": 5},
               "claims": claims, "timestamps": {"published_at": published}}
    bullets = "".join(f"- {c['claim_es']}\n" for c in claims)
    return f"{platform} post by @h on {published[:10]} mentioning Clara Brugada. {len(claims)} claim(s):\n{bullets}\nJSON:\n" \
        + json.dumps(payload)


def _claim(i: int, text: str, ctype="statistic", speaker="politician") -> dict:
    return {"claim_index": i, "claim_es": text, "claim_en": text + " (en)", "claim_type": ctype, "speaker": speaker,
            "about_politician": True, "checkable": True, "source_field": "transcript", "evidence_span": text}


SOURCES = {
    "s1": {"dp": "oriane:cnt_a:v1", "at": "2026-11-10T22:08:38Z",
           "content": _content("cnt_a", [_claim(0, "Más agua"), _claim(1, "Nuevo hospital", "promise")])},
    "s2": {"dp": "oriane:cnt_b:v1", "at": "2026-11-01T10:00:00Z", "truncated": True,
           "content": _content("cnt_b", [_claim(0, "Rentas", "policy", "uploader")], platform="instagram",
                               published="2026-11-01T10:00:00.000Z", pad="x" * 5000)},
    "s3": {"dp": "oriane:cnt_c:v1", "at": "2026-10-20T10:00:00Z", "current": False,
           "content": _content("cnt_c", [_claim(0, "Viejo")])},
}


def _transport(calls: list[str]) -> httpx.MockTransport:
    order = list(SOURCES)

    def entry(sid):
        s = SOURCES[sid]
        return {"id": sid, "kind": "source", "dataPointId": s["dp"], "occurredAt": s["at"],
                "current": s.get("current", True), "headline": s["content"].split("\n")[0], "content": None}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer past_sk_test"
        path = req.url.path.removeprefix("/api/v1")
        calls.append(path)
        if path == "/identities":
            return httpx.Response(200, json={"identities": [{"identity": "reader-pol-clara-brugada", "traits": {
                "politician": "person:MX:clara-marina-brugada-molina"}}], "hasMore": False})
        if path == "/audiences":
            return httpx.Response(200, json={"audiences": [{"slug": "pol-clara-brugada", "memoryCount": 9,
                                                            "identities": ["reader-pol-clara-brugada"]}]})
        if path == "/trace/roots":
            start = int(req.url.params.get("afterSources") or 0)
            page = order[start:start + 2]
            more = start + 2 < len(order)
            return httpx.Response(200, json={"memories": {"entries": [], "totalCount": 0, "hasMore": False,
                                                          "endCursor": None},
                                             "sources": {"entries": [entry(s) for s in page], "totalCount": 3,
                                                         "hasMore": more, "endCursor": str(start + 2) if more else None}})
        if path.startswith("/trace/nodes/"):
            s = SOURCES[path.rsplit("/", 1)[1]]
            text = s["content"]
            return httpx.Response(200, json={"content": text[:4000], "contentTruncated": len(text) > 4000})
        if path == "/recall":
            body = json.loads(req.content)
            assert body["identity"] == "reader-pol-clara-brugada"
            hits = [s for s in SOURCES.values() if s["at"] == body["occurredFrom"] == body["occurredTo"]]
            return httpx.Response(200, json={"results": [
                {"artifact": {"kind": "observation"}, "content": "obs", "sources": [{"sourceId": s["dp"]}]}
                for s in hits] + [{"artifact": {"kind": "source"}, "content": s["content"],
                                   "sources": [{"sourceId": s["dp"]}]} for s in hits]})
        return httpx.Response(404, json={"error": path})
    return httpx.MockTransport(handler)


def test_pull_exports_every_current_claim_with_system_a_ids(graph, tmp_path):
    calls: list[str] = []
    client = PastDevClient("past_sk_test", transport=_transport(calls))
    manifest = pull(client, graph, "mx", tmp_path)

    assert manifest["posts"] == 2 and manifest["claims"] == 3 and manifest["partial_posts"] == 0
    assert manifest["skipped"]["not_current"] == 1
    assert manifest["audiences"][0]["politicians"] == ["person:MX:clara-marina-brugada-molina"]
    assert calls.count("/recall") == 1  # only the truncated source needs a recall
    posts = [json.loads(line) for line in (tmp_path / "MX" / "posts.jsonl").read_text().splitlines()]
    claims = [json.loads(line) for line in (tmp_path / "MX" / "claims.jsonl").read_text().splitlines()]
    assert [p["data_point_id"] for p in posts] == ["oriane:cnt_a:v1", "oriane:cnt_b:v1"]  # newest first
    # The payload's id does not exist in System A; the name resolves to the canonical Person.
    assert {c["politician_id"] for c in claims} == {"person:MX:clara-marina-brugada-molina"}
    assert posts[1]["caption"].endswith("x" * 5000)
    assert posts[1]["url"] == "https://www.instagram.com/p/77/"
    assert claims[0] | {"views": 100} == claims[0]
    assert claims[1]["claim_id"] == "oriane:cnt_a:v1#1" and claims[1]["type"] == "promise"


def test_parse_helpers():
    assert parse_payload("no marker") is None
    assert parse_payload("x\nJSON:\n{broken") is None
    assert post_url({"platform": "tiktok", "platform_id": "1", "uploader_handle": "a"}) == \
        "https://www.tiktok.com/@a/video/1"


def test_claims_api_summary_and_filters(graph, tmp_path):
    pull(PastDevClient("past_sk_test", transport=_transport([])), graph, "MX", tmp_path)
    app = FastAPI()
    app.include_router(build_router(lambda: tmp_path))
    api = TestClient(app)

    s = api.get("/api/claims/mx/summary").json()
    assert s["available"] and s["stats"]["claims"] == 3 and s["stats"]["posts"] == 2
    assert s["politicians"][0]["politician_id"] == "person:MX:clara-marina-brugada-molina"
    assert s["stats"]["by_day"] == [{"date": "2026-11-01", "claims": 1}, {"date": "2026-11-10", "claims": 2}]

    pid = "person:MX:clara-marina-brugada-molina"
    r = api.get(f"/api/claims/MX/politicians/{pid}", params={"type": "policy"}).json()
    assert r["total"] == 1 and r["stats"]["claims"] == 3
    assert r["claims"][0]["engagement"]["views"] == 100 and r["claims"][0]["platform"] == "instagram"
    assert api.get(f"/api/claims/MX/politicians/{pid}", params={"q": "HOSPITAL"}).json()["total"] == 1
    assert api.get(f"/api/claims/MX/politicians/{pid}", params={"limit": 0}).status_code == 422
    assert api.get("/api/claims/PK/summary").json()["available"] is False
