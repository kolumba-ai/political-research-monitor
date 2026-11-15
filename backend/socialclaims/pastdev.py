"""past.dev claim memory reader: pulls every claim data point of a country project into local JSONL.

past.dev has no route that lists data points, so the reader walks the project's trace roots (one `source` node
per data point, see `GET /api/v1/trace/roots`) and reads each source's full text from `GET /api/v1/trace/nodes/{id}`.
Each data point written by the claim pipeline is `<summary>\\n\\nJSON:\\n<payload>`; only the payload is parsed.
The key is sent only in the Authorization header and never logged.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx
import structlog

from polgraph.ids import normalize_text

log = structlog.get_logger(__name__)

PAYLOAD_MARKER = "\nJSON:\n"
HEADLINE = re.compile(r"^(?P<platform>\w+) post by @(?P<handle>\S+) on (?P<date>\d{4}-\d{2}-\d{2}) mentioning (?P<name>.+?)\.")
EXPORT_FORMAT = "pastdev-claims/1"


class PastDevError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"past.dev {status}: {message}")
        self.status = status


class PastDevClient:
    def __init__(self, api_key: str | None, base_url: str = "https://api.past.dev/api/v1", timeout_s: float = 30.0,
                 max_retries: int = 4, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise PastDevError(0, "PAST_DEV_API_KEY is not set")
        self.max_retries = max_retries
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params={k: v for k, v in (params or {}).items() if v is not None})

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json={k: v for k, v in body.items() if v is not None})

    def _request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise PastDevError(0, f"transport error: {exc}") from exc
            else:
                if r.status_code < 400:
                    return r.json()
                if r.status_code != 429 and r.status_code < 500 or attempt == self.max_retries:
                    raise PastDevError(r.status_code, r.text[:300])
            time.sleep(min(2 ** attempt, 20))
        raise AssertionError("unreachable")

    def audiences(self) -> list[dict]:
        return self.get("/audiences").get("audiences", [])

    def identities(self) -> list[dict]:
        out, cursor = [], None
        while True:
            page = self.get("/identities", {"after": cursor})
            out += page.get("identities", [])
            if not page.get("hasMore") or not page.get("endCursor"):
                return out
            cursor = page["endCursor"]

    def sources(self, page_size: int = 100) -> Iterator[dict]:
        """Every source node (one per ingested data point revision) in the project."""
        cursor = None
        while True:
            page = self.get("/trace/roots", {"first": page_size, "afterSources": cursor})["sources"]
            yield from page.get("entries", [])
            if not page.get("hasMore") or not page.get("endCursor"):
                return
            cursor = page["endCursor"]

    def node(self, node_id: str) -> dict:
        return self.get(f"/trace/nodes/{node_id}")

    def recall(self, query: str, identity: str, occurred_from: str | None = None, occurred_to: str | None = None,
               limit: int = 20) -> list[dict]:
        """Costs 1 credit on Pay as you go. Returns whole documents, so it also yields untruncated source text."""
        return self.post("/recall", {"query": query, "identity": identity, "occurredFrom": occurred_from,
                                     "occurredTo": occurred_to, "limit": limit}).get("results", [])

    def full_text(self, source: dict, readers: list[str]) -> str | None:
        """A source's complete text. Trace nodes cut text at 4,000 characters; a recall pinned to the source's
        exact occurrence time returns the whole document, read as each reader identity in turn."""
        content = source.get("content")
        if content and not source.get("contentTruncated"):
            return content
        node = self.node(source["id"])
        if node.get("content") and not node.get("contentTruncated"):
            return node["content"]
        when = source.get("occurredAt")
        for reader in readers:
            for doc in self.recall(source.get("headline") or "", reader, when, when):
                ids = {x.get("sourceId") for x in doc.get("sources") or []}
                if (doc.get("artifact") or {}).get("kind") == "source" and source.get("dataPointId") in ids:
                    return doc.get("content")
        return node.get("content")


def parse_payload(content: str | None) -> dict | None:
    if not content or PAYLOAD_MARKER not in content:
        return None
    try:
        data = json.loads(content.split(PAYLOAD_MARKER, 1)[1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def summary_claims(content: str | None) -> list[str]:
    """Claim texts from the bullet list before the JSON payload (used only when the payload is unreadable)."""
    head = (content or "").split(PAYLOAD_MARKER, 1)[0]
    return [line[2:].strip() for line in head.splitlines() if line.startswith("- ") and line[2:].strip()]


def post_url(source: dict) -> str | None:
    platform, pid, handle = source.get("platform"), source.get("platform_id"), source.get("uploader_handle")
    if not pid:
        return None
    if platform == "instagram":
        return f"https://www.instagram.com/p/{pid}/"
    if platform == "tiktok" and handle:
        return f"https://www.tiktok.com/@{handle}/video/{pid}"
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={pid}"
    return None


@dataclass
class PoliticianResolver:
    """Maps a payload's politician to a System A `Person` id; never invents one."""
    graph: object
    country: str
    known: dict[str, str] = field(default_factory=dict)

    def __call__(self, politician: dict) -> str | None:
        claimed, name = politician.get("system_a_person_id"), politician.get("name") or ""
        key = f"{claimed}|{name}"
        if key in self.known:
            return self.known[key]
        hit = None
        if claimed and self.graph.get_node(claimed):
            hit = claimed
        elif name:
            from .systema import AmbiguousName, resolve_name
            try:
                hit = resolve_name(self.graph, name, self.country)
            except AmbiguousName:
                hit = None
        self.known[key] = hit
        return hit


def _sort_key(row: dict) -> str:
    return row.get("published_at") or ""


def pull(client: PastDevClient, graph, country: str, out_dir: Path) -> dict:
    """Read all current claim data points and write `<out_dir>/<CC>/{posts,claims}.jsonl` + `manifest.json`."""
    country = country.upper()
    resolve = PoliticianResolver(graph, country)
    audience_of = {}
    for ident in client.identities():
        pol = (ident.get("traits") or {}).get("politician")
        if pol:
            audience_of[ident["identity"]] = pol
    audiences = [{"slug": a["slug"], "name": a.get("name"), "memories": a.get("memoryCount"),
                  "politicians": sorted({audience_of[i] for i in a.get("identities") or [] if i in audience_of})}
                 for a in client.audiences()]

    readers = sorted(audience_of) or ["pastdev-claims-reader"]
    posts: dict[str, dict] = {}
    skipped = {"not_current": 0, "no_payload": 0, "other_country": 0, "unresolved_politician": 0}
    partial = 0
    for src in client.sources():
        if not src.get("current"):
            skipped["not_current"] += 1
            continue
        content = client.full_text(src, readers)
        payload = parse_payload(content)
        if not payload:
            texts = summary_claims(content)
            if not texts:
                skipped["no_payload"] += 1
                continue
            partial += 1
            m = HEADLINE.match(content or "")
            payload = {"politician": {"name": m["name"]} if m else {}, "partial": True,
                       "source": {"platform": m["platform"], "uploader_handle": m["handle"]} if m else {},
                       "claims": [{"claim_index": i, "claim_es": t} for i, t in enumerate(texts)]}
        pol = payload.get("politician") or {}
        if (pol.get("country") or country).upper() != country:
            skipped["other_country"] += 1
            continue
        politician_id = resolve(pol)
        if not politician_id:
            skipped["unresolved_politician"] += 1
        s = payload.get("source") or {}
        ts = payload.get("timestamps") or {}
        data_point_id = src.get("dataPointId") or s.get("oriane_content_id")
        post = {
            "data_point_id": data_point_id, "trace_source_id": src["id"], "politician_id": politician_id,
            "politician_name": pol.get("name"), "payload_person_id": pol.get("system_a_person_id"),
            "country": country, "oriane_content_id": s.get("oriane_content_id"), "platform": s.get("platform"),
            "platform_id": s.get("platform_id"), "url": post_url(s), "format": s.get("format"),
            "uploader_handle": s.get("uploader_handle"), "uploader_name": s.get("uploader_name"),
            "caption": s.get("caption"), "transcript_excerpt": s.get("transcript_excerpt"),
            "published_at": s.get("published_at") or ts.get("published_at") or src.get("occurredAt"),
            "engagement": payload.get("engagement_snapshot") or {},
            "claims_extracted_at": ts.get("claims_extracted_at"), "ingested_at": src.get("createdAt"),
            "partial": bool(payload.get("partial")), "claims": [],
        }
        for c in payload.get("claims") or []:
            post["claims"].append({
                "claim_id": f"{data_point_id}#{c.get('claim_index', len(post['claims']))}",
                "claim_index": c.get("claim_index"), "text": c.get("claim_es"), "text_en": c.get("claim_en"),
                "type": c.get("claim_type"), "speaker": c.get("speaker"),
                "about_politician": c.get("about_politician"), "checkable": c.get("checkable"),
                "source_field": c.get("source_field"), "evidence_span": c.get("evidence_span")})
        posts[data_point_id] = post

    rows = sorted(posts.values(), key=_sort_key, reverse=True)
    claims = []
    for p in rows:
        base = {k: p[k] for k in ("data_point_id", "politician_id", "politician_name", "platform", "url",
                                  "uploader_handle", "uploader_name", "published_at")}
        for c in p["claims"]:
            claims.append({**c, **base, "views": (p["engagement"] or {}).get("views")})

    target = Path(out_dir) / country
    target.mkdir(parents=True, exist_ok=True)
    for name, items in (("posts.jsonl", rows), ("claims.jsonl", claims)):
        tmp = target / f".{name}.tmp"
        tmp.write_text("".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items),
                       encoding="utf-8")
        tmp.replace(target / name)
    by_pol: dict[str, dict] = {}
    for p in rows:
        key = p["politician_id"] or f"unresolved:{normalize_text(p['politician_name'] or '')}"
        agg = by_pol.setdefault(key, {"politician_id": p["politician_id"], "name": p["politician_name"],
                                      "posts": 0, "claims": 0, "first": None, "last": None})
        agg["posts"] += 1
        agg["claims"] += len(p["claims"])
        d = p["published_at"]
        if d:
            agg["first"] = min(filter(None, [agg["first"], d]))
            agg["last"] = max(filter(None, [agg["last"], d]))
    manifest = {"format": EXPORT_FORMAT, "country": country, "pulled_at": datetime.now(timezone.utc).isoformat(),
                "source": "past.dev", "posts": len(rows), "claims": len(claims), "partial_posts": partial,
                "skipped": skipped,
                "audiences": audiences, "politicians": sorted(by_pol.values(), key=lambda a: -a["claims"])}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("pastdev.pulled", country=country, posts=len(rows), claims=len(claims), skipped=skipped)
    return manifest
