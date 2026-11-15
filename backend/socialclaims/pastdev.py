"""past.dev claim memory reader: pulls every claim, document and evidence-link data point of a country project into
local JSONL.

past.dev has no route that lists data points, so the reader walks the project's trace roots (one `source` node
per data point, see `GET /api/v1/trace/roots`) and reads each source's full text from `GET /api/v1/trace/nodes/{id}`.
Each data point written by the claim pipeline is `<summary>\\n\\nJSON:\\n<payload>`; only the payload is parsed.
The key is sent only in the Authorization header and never logged.

past.dev records carry a post's link and account only where System B stored a URL (TikTok). `PostgresLookup` reads
the missing platform id and handle from the System B `post` table (read-only), together with each proposition's
structured fields, which the storyline hard rules compare.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx
import structlog

from polgraph.exa_gateway import PgRateLimiter, retry_after_seconds
from polgraph.ids import normalize_text

log = structlog.get_logger(__name__)

PAYLOAD_MARKER = "\nJSON:\n"
HEADLINE = re.compile(r"^(?P<platform>\w+) post by @(?P<handle>\S+) on (?P<date>\d{4}-\d{2}-\d{2}) mentioning (?P<name>.+?)\.")
EXPORT_FORMAT = "pastdev-claims/1"
EVIDENCE_CLAIM_FORMAT = "evidencegraph-claim/1"  # System C records (evidencegraph/project.py)
EVIDENCE_DOCUMENT_FORMAT = "evidencegraph-document/1"
EVIDENCE_LINK_FORMAT = "evidencegraph-link/1"
# Data point id prefixes of System C documents and evidence links; skipped unread when `evidence=False`.
EVIDENCE_ONLY_PREFIXES = ("doc-", "lnk-")
DOCUMENT_FIELDS = ("document_id", "url", "publisher", "source_class", "authority_tier", "licence_class", "title",
                   "published_at", "date_confidence", "retrieved_at", "date_is_retrieval", "content_sha256", "summary")
LINK_FIELDS = ("link_id", "proposition_id", "document_id", "document_datapoint", "stance", "confidence", "quote",
               "rationale", "source_class", "authority_tier", "assessed_at", "mapper_version")
PROPOSITION_FIELDS = ("subject", "predicate", "negated", "vals", "place", "period_start", "period_end", "qualifiers",
                      "topic_id")
EVIDENCE_SPEAKER = {"speaker": "politician", "quoted": "politician_quoted"}  # relation_to_politician -> speaker


class PastDevError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"past.dev {status}: {message}")
        self.status = status


class PastDevClient:
    def __init__(self, api_key: str | None, base_url: str = "https://api.past.dev/api/v1", timeout_s: float = 30.0,
                 max_retries: int = 4, transport: httpx.BaseTransport | None = None, sleep=time.sleep,
                 rate_limiter_database_url: str | None = None, requests_per_second: float = 2.0, limiter=None):
        if not api_key:
            raise PastDevError(0, "PAST_DEV_API_KEY is not set")
        self.max_retries = max_retries
        self.sleep = sleep
        self.limiter = limiter if limiter is not None else (
            PgRateLimiter(rate_limiter_database_url, f"pastdev:{api_key}", requests_per_second)
            if rate_limiter_database_url else None
        )
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params={k: v for k, v in (params or {}).items() if v is not None})

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json={k: v for k, v in body.items() if v is not None})

    def put(self, path: str, body: dict) -> dict:
        return self._request("PUT", path, json=body)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(self.max_retries + 1):
            try:
                if self.limiter is not None:
                    self.limiter.wait()
                r = self.http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise PastDevError(0, f"transport error: {exc}") from exc
            else:
                if r.status_code < 400:
                    return r.json()
                if r.status_code != 429 and r.status_code < 500 or attempt == self.max_retries:
                    raise PastDevError(r.status_code, r.text[:300])
                delay = retry_after_seconds(r.headers) if r.status_code in (429, 503) else None
                self.sleep(min(2 ** attempt, 20) if delay is None else delay)
                continue
            self.sleep(min(2 ** attempt, 20))
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


class PostgresLookup:
    """Read-only lookups in the System B database for what past.dev records leave out."""

    def __init__(self, url: str, connect=None):
        self.url, self._connect = url, connect

    def _conn(self):
        if self._connect:
            return self._connect(self.url)
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=20,
                               options="-c default_transaction_read_only=on")
        conn.read_only = True  # also holds behind a connection pooler, which may drop startup options
        return conn

    def _rows(self, sql: str, ids: list[str], key: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not ids:
            return out
        with self._conn() as conn:
            for i in range(0, len(ids), 5000):
                for row in conn.execute(sql, (ids[i:i + 5000],)).fetchall():
                    out[row[key]] = dict(row)
        return out

    def posts(self, oriane_ids: list[str]) -> dict[str, dict]:
        return self._rows("SELECT oriane_id, platform, platform_id, url, profile_handle, format FROM post "
                          "WHERE oriane_id = ANY(%s)", oriane_ids, "oriane_id")

    def propositions(self, proposition_ids: list[str]) -> dict[str, dict]:
        return self._rows("SELECT proposition_id, subject, predicate, negated, vals, place, period_start, period_end, "
                          "qualifiers, topic_id FROM proposition WHERE proposition_id = ANY(%s)",
                          proposition_ids, "proposition_id")


def fill_post_links(posts: list[dict], known: dict[str, dict]) -> int:
    """Complete each post's platform id, account and link from System B's `post` rows; returns how many gained a link."""
    filled = 0
    for p in posts:
        row = known.get(p.get("oriane_content_id") or "")
        if not row:
            continue
        had = bool(p.get("url"))
        p["platform"] = p.get("platform") or row.get("platform")
        p["platform_id"] = p.get("platform_id") or row.get("platform_id")
        p["uploader_handle"] = p.get("uploader_handle") or row.get("profile_handle")
        p["format"] = p.get("format") or row.get("format")
        p["url"] = p.get("url") or row.get("url") or post_url(p)
        filled += bool(p["url"]) and not had
    return filled


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


def _evidence_claim_posts(src: dict, payload: dict) -> list[tuple[str, dict]]:
    """A System C claim record (`evidencegraph-claim/1`) as one post-shaped payload per post that stated it, keyed by
    post x politician so claims of several propositions from one post merge into one exported post."""
    by_post: dict[str, list[dict]] = {}
    for c in payload.get("claims") or []:
        by_post.setdefault(c.get("oriane_id") or "", []).append(c)
    person = (payload.get("politician") or {}).get("system_a_person_id") or ""
    return [(f"post:{oid}:{person}", {
        "politician": payload.get("politician") or {},
        "source": {"oriane_content_id": oid or None, "platform": cs[0].get("platform"), "url": cs[0].get("url"),
                   "published_at": cs[0].get("stated_at")},
        "claims": [{"claim_index": i, "claim_id": c.get("claim_id"), "claim_es": c.get("text"), "claim_en": c.get("text_en"),
                    "claim_type": c.get("type"), "evidence_span": c.get("quote"),
                    "proposition_id": payload.get("proposition_id"), "canonical_text": payload.get("canonical_text"),
                    "relation_to_politician": c.get("relation_to_politician"),
                    "speaker": EVIDENCE_SPEAKER.get(c.get("relation_to_politician")),
                    "about_politician": c.get("relation_to_politician") in ("speaker", "quoted", "subject")}
                   for i, c in enumerate(cs)],
    }) for oid, cs in by_post.items()]


def _jsonable(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def pull(client: PastDevClient, graph, country: str, out_dir: Path, workers: int = 1, evidence: bool = True,
         lookup: PostgresLookup | None = None) -> dict:
    """Read all current data points and write `<out_dir>/<CC>/{posts,claims,documents,evidence,propositions}.jsonl`
    + `manifest.json`. `evidence=False` leaves System C documents and evidence links unread (a faster pull)."""
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
    skipped = {"not_current": 0, "no_payload": 0, "other_country": 0, "unresolved_politician": 0,
               "evidence_records": 0}
    documents: dict[str, dict] = {}
    links: dict[str, dict] = {}
    partial = 0
    wanted = []
    for src in client.sources():
        if not src.get("current"):
            skipped["not_current"] += 1
        elif not evidence and str(src.get("dataPointId") or "").startswith(EVIDENCE_ONLY_PREFIXES):
            skipped["evidence_records"] += 1
        else:
            wanted.append(src)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        contents = list(pool.map(lambda src: client.full_text(src, readers), wanted))
    for src, content in zip(wanted, contents):
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
        fmt = str(payload.get("format") or "")
        if evidence and fmt == EVIDENCE_DOCUMENT_FORMAT:
            documents[src["dataPointId"]] = {"document_datapoint": src["dataPointId"],
                                             **{k: payload.get(k) for k in DOCUMENT_FIELDS}}
            continue
        if evidence and fmt == EVIDENCE_LINK_FORMAT:
            pol = payload.get("politician") or {}
            links[src["dataPointId"]] = {"data_point_id": src["dataPointId"], "politician_id": resolve(pol),
                                         "politician_name": pol.get("name"), **{k: payload.get(k) for k in LINK_FIELDS}}
            continue
        if fmt.startswith("evidencegraph-") and fmt != EVIDENCE_CLAIM_FORMAT:
            skipped["evidence_records"] += 1
            continue
        entries = (_evidence_claim_posts(src, payload) if fmt == EVIDENCE_CLAIM_FORMAT else
                   [(src.get("dataPointId") or (payload.get("source") or {}).get("oriane_content_id"), payload)])
        for data_point_id, payload in entries:
            pol = payload.get("politician") or {}
            if (pol.get("country") or country).upper() != country:
                skipped["other_country"] += 1
                continue
            politician_id = resolve(pol)
            if not politician_id:
                skipped["unresolved_politician"] += 1
            s = payload.get("source") or {}
            ts = payload.get("timestamps") or {}
            post = {
                "data_point_id": data_point_id, "trace_source_id": src["id"], "politician_id": politician_id,
                "politician_name": pol.get("name"), "payload_person_id": pol.get("system_a_person_id"),
                "country": country, "oriane_content_id": s.get("oriane_content_id"), "platform": s.get("platform"),
                "platform_id": s.get("platform_id"), "url": s.get("url") or post_url(s), "format": s.get("format"),
                "uploader_handle": s.get("uploader_handle"), "uploader_name": s.get("uploader_name"),
                "caption": s.get("caption"), "transcript_excerpt": s.get("transcript_excerpt"),
                "published_at": s.get("published_at") or ts.get("published_at") or src.get("occurredAt"),
                "engagement": payload.get("engagement_snapshot") or {},
                "claims_extracted_at": ts.get("claims_extracted_at"), "ingested_at": src.get("createdAt"),
                "partial": bool(payload.get("partial")), "claims": [],
            }
            for c in payload.get("claims") or []:
                post["claims"].append({
                    "claim_id": c.get("claim_id") or f"{data_point_id}#{c.get('claim_index', len(post['claims']))}",
                    "claim_index": c.get("claim_index"), "text": c.get("claim_es"), "text_en": c.get("claim_en"),
                    "type": c.get("claim_type"), "speaker": c.get("speaker"),
                    "about_politician": c.get("about_politician"), "checkable": c.get("checkable"),
                    "source_field": c.get("source_field"), "evidence_span": c.get("evidence_span"),
                    "proposition_id": c.get("proposition_id"), "canonical_text": c.get("canonical_text"),
                    "relation_to_politician": c.get("relation_to_politician")})
            if data_point_id in posts:  # the same post stated several System C propositions
                posts[data_point_id]["claims"] += post["claims"]
            else:
                posts[data_point_id] = post

    rows = sorted(posts.values(), key=_sort_key, reverse=True)
    post_links = {"source": None, "filled": 0}
    propositions: list[dict] = []
    if lookup is not None:
        try:
            known = lookup.posts(sorted({p["oriane_content_id"] for p in rows if p.get("oriane_content_id")}))
            post_links = {"source": "System B Postgres", "filled": fill_post_links(rows, known)}
            prop_ids = sorted({c["proposition_id"] for p in rows for c in p["claims"] if c.get("proposition_id")})
            propositions = [{"proposition_id": pid, **{k: _jsonable(row.get(k)) for k in PROPOSITION_FIELDS}}
                            for pid, row in sorted(lookup.propositions(prop_ids).items())]
        except Exception as exc:  # the export is still valid without the lookup; say so instead of failing the pull
            post_links = {"source": None, "filled": 0, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            log.warning("pastdev.post_lookup_failed", error=post_links["error"])
    post_links["without_link"] = sum(1 for p in rows if not p.get("url"))
    claims = []
    for p in rows:
        base = {k: p[k] for k in ("data_point_id", "politician_id", "politician_name", "platform", "platform_id",
                                  "oriane_content_id", "url", "uploader_handle", "uploader_name", "published_at")}
        for c in p["claims"]:
            claims.append({**c, **base, "views": (p["engagement"] or {}).get("views")})
    used = {l["document_datapoint"] for l in links.values()}
    evidence_rows = sorted(links.values(), key=lambda l: (l.get("proposition_id") or "", l["data_point_id"]))
    document_rows = sorted(documents.values(), key=lambda d: d["document_datapoint"])

    target = Path(out_dir) / country
    target.mkdir(parents=True, exist_ok=True)
    files = [("posts.jsonl", rows), ("claims.jsonl", claims)]
    if evidence:
        files += [("documents.jsonl", document_rows), ("evidence.jsonl", evidence_rows)]
    if propositions:
        files.append(("propositions.jsonl", propositions))
    for name, items in files:
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
                "skipped": skipped, "post_links": post_links,
                "evidence": {"pulled": evidence, "links": len(evidence_rows), "documents": len(document_rows),
                             "links_without_document": sum(1 for l in evidence_rows
                                                           if l["document_datapoint"] not in documents),
                             "documents_unused": len(set(documents) - used)},
                "propositions": len(propositions),
                "audiences": audiences, "politicians": sorted(by_pol.values(), key=lambda a: -a["claims"])}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("pastdev.pulled", country=country, posts=len(rows), claims=len(claims), skipped=skipped)
    return manifest
