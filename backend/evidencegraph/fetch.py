"""Robots-aware publisher fetching with rights-aware document version storage."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from polgraph.evidence import authority_tier
from polgraph.exa_gateway import PgRateLimiter
from polgraph.ids import canonical_url, registrable_domain, short_hash, source_id

from .config import EvidenceSettings
from .registry import lookup

log = structlog.get_logger(__name__)
REDIRECT_CODES = {301, 302, 303, 307, 308}
BLOCKED_CODES = {401, 402, 403, 451}
BLOCKED_PAGE = re.compile(r"captcha|verify you are human|sign in to continue|log in to continue|login required",
                          re.IGNORECASE)
_TRAFILATURA_WARNING_LOCK = Lock()
_TRAFILATURA_WARNING_EMITTED = False


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.language: str | None = None
        self.links: list[str] = []
        self.jsonld: list[str] = []
        self._in_title = False
        self._in_jsonld = False
        self._skip_depth = 0
        self._script_parts: list[str] = []
        self.has_password_input = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "html":
            self.language = attrs.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "input" and (attrs.get("type") or "").lower() == "password":
            self.has_password_input = True
        elif tag in {"script", "style", "noscript"}:
            if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._script_parts = []
            else:
                self._skip_depth += 1
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
            if key and attrs.get("content"):
                self.meta[key] = attrs["content"].strip()
        elif tag == "link" and "canonical" in attrs.get("rel", "").lower():
            self.canonical = attrs.get("href")
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._script_parts))
            self._in_jsonld = False
        elif tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if self._in_jsonld:
            self._script_parts.append(data)
        elif not self._skip_depth and not self._in_title:
            self.text_parts.append(data)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.combine(date.fromisoformat(value[:10]), datetime.min.time())
            except ValueError:
                return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _jsonld_dates(values: list[str]) -> tuple[str | None, str | None]:
    published = modified = None

    def visit(value):
        nonlocal published, modified
        if isinstance(value, dict):
            published = published or value.get("datePublished")
            modified = modified or value.get("dateModified")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for raw in values:
        try:
            visit(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return published, modified


def _date_metadata(parser: _PageParser, url: str, exa_date: str | None) -> tuple[datetime | None, datetime | None, str, str]:
    json_published, json_modified = _jsonld_dates(parser.jsonld)
    published = _parse_date(json_published)
    modified = _parse_date(json_modified)
    if published:
        return published, modified, "jsonld", "high"
    og_published = parser.meta.get("article:published_time")
    og_modified = parser.meta.get("article:modified_time")
    published = _parse_date(og_published)
    modified = modified or _parse_date(og_modified)
    if published:
        return published, modified, "open_graph", "high"
    for key in ("date", "pubdate", "dc.date.issued", "citation_publication_date"):
        published = _parse_date(parser.meta.get(key))
        if published:
            return published, modified, "meta", "high"
    match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", url)
    if match:
        published = _parse_date("-".join(match.groups()))
    else:
        match = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", url)
        published = _parse_date(match.group(1)) if match else None
    if published:
        return published, modified, "url", "medium"
    published = _parse_date(exa_date)
    if published:
        return published, modified, "exa", "low"
    return None, modified, "none", "none"


def _html_text(raw: bytes, parser: _PageParser) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(raw, include_comments=False, include_tables=True)
    except ImportError:
        global _TRAFILATURA_WARNING_EMITTED
        with _TRAFILATURA_WARNING_LOCK:
            if not _TRAFILATURA_WARNING_EMITTED:
                log.warning("fetch.trafilatura_missing")
                _TRAFILATURA_WARNING_EMITTED = True
        text = None
    if text:
        return text.strip()
    return re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()


def _pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    pages = PdfReader(BytesIO(raw)).pages
    return "\n".join(f"--- page {index + 1} ---\n{page.extract_text() or ''}" for index, page in enumerate(pages))


def _simhash(text: str) -> str:
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return "0" * 64
    weights = [0] * 64
    for token in tokens:
        value = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    result = sum((1 << bit) for bit, weight in enumerate(weights) if weight >= 0)
    return f"{result:064b}"


class Fetcher:
    def __init__(self, conn, settings: EvidenceSettings, client: httpx.Client | None = None, limiter_factory=None):
        self.conn = conn
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.fetch_timeout_s, follow_redirects=False)
        self._owns_client = client is None
        self.limiter_factory = limiter_factory or (
            lambda host, rps: PgRateLimiter(settings.database_url, f"fetch:{host}", rps)
        )
        self._robots: dict[str, tuple[RobotFileParser | None, bool, float | None]] = {}
        self._limiters: dict[str, object] = {}

    def close(self):
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.sha256(canonical_url(url).encode()).hexdigest()

    def enqueue(self, url: str, priority: float = 0) -> str:
        key = self._url_hash(url)
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO ev_discovered_url(url_hash,request_id,url,discovered_at) VALUES (%s,'',%s,now()) "
                "ON CONFLICT (url_hash,request_id) DO UPDATE SET url=EXCLUDED.url",
                (key, url),
            )
            self.conn.execute(
                "INSERT INTO ev_fetch_queue(url_hash,url,priority,enqueued_at,next_attempt_at) "
                "VALUES (%s,%s,%s,now(),now()) ON CONFLICT (url_hash) DO NOTHING",
                (key, url, priority),
            )
        return key

    def _robots_allowed(self, url: str) -> tuple[bool, float | None]:
        parts = urlsplit(url)
        host_key = parts.netloc.lower()
        cached = self._robots.get(host_key)
        if cached is None:
            robots_url = f"{parts.scheme or 'https'}://{parts.netloc}/robots.txt"
            parser = None
            allow_all = False
            delay = None
            try:
                response = self.client.get(robots_url, headers={"User-Agent": self.settings.fetch_user_agent},
                                           timeout=self.settings.fetch_timeout_s)
                if 400 <= response.status_code < 500:
                    allow_all = True
                elif response.is_success:
                    parser = RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(response.text.splitlines())
                    delay = parser.crawl_delay(self.settings.fetch_user_agent)
            except httpx.HTTPError:
                pass
            cached = (parser, allow_all, delay)
            self._robots[host_key] = cached
        parser, allow_all, delay = cached
        allowed = parser.can_fetch(self.settings.fetch_user_agent, url) if parser else allow_all
        return allowed, delay

    def _wait_domain(self, url: str, registry_row: dict | None, robots_delay: float | None):
        host = (urlsplit(url).hostname or "").lower()
        delay = max(self.settings.fetch_min_interval_s, registry_row.get("crawl_delay") or 0 if registry_row else 0,
                    robots_delay or 0)
        limiter = self._limiters.get(host)
        if limiter is None:
            limiter = self.limiter_factory(host, 1.0 / max(delay, 0.001))
            self._limiters[host] = limiter
        limiter.wait()

    def _retry(self, key: str, url: str, error: str) -> dict:
        row = self.conn.execute("SELECT attempts FROM ev_fetch_queue WHERE url_hash=%s", (key,)).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        status = "queued" if attempts < 3 else "failed"
        delay = min(60 * (2 ** (attempts - 1)), 3600)
        self.conn.execute(
            "INSERT INTO ev_fetch_queue(url_hash,url,attempts,status,last_error,enqueued_at,next_attempt_at) "
            "VALUES (%s,%s,%s,%s,%s,now(),now() + (%s * interval '1 second')) "
            "ON CONFLICT (url_hash) DO UPDATE SET attempts=EXCLUDED.attempts,status=EXCLUDED.status,"
            "last_error=EXCLUDED.last_error,next_attempt_at=EXCLUDED.next_attempt_at",
            (key, url, attempts, status, error[:1000], delay),
        )
        return {"url": url, "fetch_status": status, "attempts": attempts, "error": error}

    def _record_status(self, key: str, url: str, status: str, error: str | None = None):
        self.conn.execute(
            "INSERT INTO ev_fetch_queue(url_hash,url,attempts,status,last_error,enqueued_at,next_attempt_at) "
            "VALUES (%s,%s,0,%s,%s,now(),now()) ON CONFLICT (url_hash) DO UPDATE SET status=EXCLUDED.status,"
            "last_error=EXCLUDED.last_error",
            (key, url, status, error),
        )

    def _document(self, document_id: str, discovered_url: str, resolved_url: str, registry_row: dict | None,
                  metadata: dict, fetch_status: str, published: datetime | None, modified: datetime | None):
        now = datetime.now(timezone.utc)
        host = (urlsplit(resolved_url).hostname or "").lower()
        licence = registry_row["licence_class"] if registry_row else "unknown"
        full_text = bool(registry_row and registry_row["can_store_full_text"])
        tier = registry_row["authority_tier"] if registry_row else authority_tier(resolved_url)
        values = (document_id, canonical_url(discovered_url), canonical_url(resolved_url), host,
                  registry_row["source_class"] if registry_row else None, tier, licence, full_text,
                  metadata.get("title"), metadata.get("authors") or [], metadata.get("language"),
                  metadata.get("outbound_links") or [], metadata["date_origin"], metadata["date_confidence"],
                  fetch_status, published, modified, now)
        self.conn.execute(
            "INSERT INTO ev_document(document_id,canonical_url,resolved_url,publisher_domain,source_class,"
            "authority_tier,licence_class,can_store_full_text,title,authors,language,outbound_links,date_origin,"
            "date_confidence,fetch_status,published_at,modified_at,first_seen_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (document_id) DO UPDATE SET resolved_url=EXCLUDED.resolved_url,"
            "publisher_domain=EXCLUDED.publisher_domain,source_class=EXCLUDED.source_class,"
            "authority_tier=EXCLUDED.authority_tier,licence_class=EXCLUDED.licence_class,"
            "can_store_full_text=EXCLUDED.can_store_full_text,title=EXCLUDED.title,"
            "authors=EXCLUDED.authors,language=EXCLUDED.language,outbound_links=EXCLUDED.outbound_links,"
            "date_origin=EXCLUDED.date_origin,date_confidence=EXCLUDED.date_confidence,"
            "fetch_status=EXCLUDED.fetch_status,published_at=EXCLUDED.published_at,modified_at=EXCLUDED.modified_at",
            values,
        )

    def _document_stub(self, document_id: str, url: str, registry_row: dict | None, status: str):
        host = (urlsplit(url).hostname or "").lower()
        licence = registry_row["licence_class"] if registry_row else "unknown"
        self.conn.execute(
            "INSERT INTO ev_document(document_id,canonical_url,resolved_url,publisher_domain,source_class,"
            "authority_tier,licence_class,can_store_full_text,date_origin,date_confidence,fetch_status,first_seen_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'none','none',%s,now()) "
            "ON CONFLICT (document_id) DO UPDATE SET publisher_domain=EXCLUDED.publisher_domain,"
            "source_class=EXCLUDED.source_class,authority_tier=EXCLUDED.authority_tier,"
            "licence_class=EXCLUDED.licence_class,can_store_full_text=EXCLUDED.can_store_full_text,"
            "fetch_status=EXCLUDED.fetch_status",
            (document_id, canonical_url(url), canonical_url(url), host,
             registry_row["source_class"] if registry_row else None,
             registry_row["authority_tier"] if registry_row else authority_tier(url), licence,
             bool(registry_row and registry_row["can_store_full_text"]), status),
        )

    def _dup_cluster(self, document_id: str, simhash: str, exclude: list[str]) -> str:
        match = self.conn.execute(
            "SELECT d.document_id,d.dup_cluster_id FROM ev_document d "
            "JOIN LATERAL (SELECT simhash FROM ev_document_version WHERE document_id=d.document_id "
            "ORDER BY retrieved_at DESC LIMIT 1) v ON true "
            "WHERE NOT (d.document_id = ANY(%s::text[])) AND bit_count(v.simhash # %s::bit(64)) <= 6 "
            "ORDER BY d.first_seen_at,d.document_id LIMIT 1",
            (exclude, simhash),
        ).fetchone()
        return (match["dup_cluster_id"] or match["document_id"]) if match else document_id

    def _store_version(self, document_id: str, text: str | None, raw: bytes, content_sha: str,
                       simhash: str | None, status: int, can_store: bool, is_text: bool) -> tuple[str | None, bool]:
        exists = self.conn.execute(
            "SELECT snapshot_path FROM ev_document_version WHERE document_id=%s AND content_sha256=%s",
            (document_id, content_sha),
        ).fetchone()
        if exists:
            return exists["snapshot_path"], False
        snapshot_path = None
        if can_store and is_text:
            directory = Path(self.settings.snapshot_dir) / document_id
            directory.mkdir(parents=True, exist_ok=True)
            text_path = directory / f"{content_sha}.txt"
            text_path.write_text(text or "", encoding="utf-8")
            with gzip.open(directory / f"{content_sha}.raw.gz", "wb") as compressed:
                compressed.write(raw)
            snapshot_path = str(text_path)
        retrieved_at = datetime.now(timezone.utc)
        cluster = self._dup_cluster(document_id, simhash, [document_id]) if simhash is not None else None
        self.conn.execute(
            "INSERT INTO ev_document_version(document_id,content_sha256,snapshot_path,http_status,bytes,simhash,retrieved_at) "
            "VALUES (%s,%s,%s,%s,%s,%s::bit(64),%s)",
            (document_id, content_sha, snapshot_path, status, len(raw), simhash, retrieved_at),
        )
        self.conn.execute("UPDATE ev_document SET dup_cluster_id=%s WHERE document_id=%s", (cluster, document_id))
        return snapshot_path, True

    def fetch(self, url: str, exa_published_date: str | None = None) -> dict:
        key = self.enqueue(url)
        original_id = source_id(url)
        registry_row = lookup(self.conn, url)
        if exa_published_date is None:
            discovered = self.conn.execute(
                "SELECT exa_published_date FROM ev_discovered_url WHERE url_hash=%s "
                "AND exa_published_date IS NOT NULL "
                "ORDER BY discovered_at DESC LIMIT 1", (key,)
            ).fetchone()
            exa_published_date = discovered["exa_published_date"] if discovered else None
        if registry_row and registry_row["list"] == "block":
            self._record_status(key, url, "blocked", "domain is on the registry block list")
            self._document_stub(original_id, url, registry_row, "blocked")
            return {"url": url, "document_id": original_id, "fetch_status": "blocked", "snapshot_path": None}
        current = url
        response = None
        raw = b""
        try:
            for redirects in range(6):
                current_registry = lookup(self.conn, current)
                if current_registry and current_registry["list"] == "block":
                    error = "redirect to blocked domain"
                    self._record_status(key, url, "blocked", error)
                    self._document_stub(original_id, url, registry_row, "blocked")
                    return {"url": url, "document_id": original_id, "fetch_status": "blocked",
                            "error": error, "snapshot_path": None}
                allowed, robots_delay = self._robots_allowed(current)
                if not allowed:
                    self._record_status(key, url, "robots_disallowed", None)
                    self._document_stub(original_id, url, registry_row, "robots_disallowed")
                    return {"url": url, "document_id": original_id, "fetch_status": "robots_disallowed",
                            "snapshot_path": None}
                self._wait_domain(current, current_registry, robots_delay)
                with self.client.stream("GET", current,
                                        headers={"User-Agent": self.settings.fetch_user_agent},
                                        timeout=self.settings.fetch_timeout_s) as response:
                    if response.status_code in REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location or redirects == 5:
                            return self._retry(key, url, "redirect limit exceeded")
                        current = urljoin(current, location)
                        continue
                    if response.status_code in BLOCKED_CODES:
                        self._record_status(key, url, "blocked", f"HTTP {response.status_code}")
                        self._document_stub(original_id, url, registry_row, "blocked")
                        return {"url": url, "document_id": original_id, "fetch_status": "blocked",
                                "http_status": response.status_code, "snapshot_path": None}
                    raw_parts = []
                    size = 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if size > self.settings.fetch_max_bytes:
                            return self._retry(key, url, "response exceeds fetch_max_bytes")
                        raw_parts.append(part)
                    raw = b"".join(raw_parts)
                    response_status = response.status_code
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    headers = response.headers
                break
            else:
                return self._retry(key, url, "redirect limit exceeded")
        except httpx.HTTPError as exc:
            return self._retry(key, url, str(exc))
        if response_status < 200 or response_status >= 300:
            return self._retry(key, url, f"HTTP {response_status}")
        is_pdf = content_type == "application/pdf" or urlsplit(current).path.lower().endswith(".pdf")
        is_html = content_type in ("text/html", "application/xhtml+xml") or not content_type
        parser = _PageParser()
        metadata = {"title": None, "authors": [], "language": None, "outbound_links": [],
                    "date_origin": "none", "date_confidence": "none"}
        text = None
        try:
            if is_html:
                parser.feed(raw.decode("utf-8", errors="replace"))
                json_author = None
                for raw_json in parser.jsonld:
                    try:
                        candidate = json.loads(raw_json)
                        if isinstance(candidate, dict):
                            author = candidate.get("author")
                            json_author = author if isinstance(author, str) else (
                                author[0].get("name") if isinstance(author, list) and author and isinstance(author[0], dict)
                                else author.get("name") if isinstance(author, dict) else None)
                    except (ValueError, TypeError, IndexError):
                        pass
                title = parser.meta.get("og:title") or "".join(parser.title_parts).strip()
                author = parser.meta.get("author") or json_author
                published, modified, origin, confidence = _date_metadata(parser, current, exa_published_date)
                resolved_url = current
                if parser.canonical:
                    canonical_candidate = urljoin(current, parser.canonical)
                    if registrable_domain(canonical_candidate) == registrable_domain(current):
                        canonical_registry = lookup(self.conn, canonical_candidate)
                        if canonical_registry and canonical_registry["list"] == "block":
                            error = "canonical target is block-listed"
                            self._record_status(key, url, "blocked", error)
                            self._document_stub(original_id, url, registry_row, "blocked")
                            return {"url": url, "document_id": original_id, "fetch_status": "blocked",
                                    "error": error, "http_status": response_status, "snapshot_path": None}
                        resolved_url = canonical_candidate
                    else:
                        log.warning("fetch.canonical_ignored", url=current, canonical=canonical_candidate)
                metadata = {
                    "title": title or None, "authors": [author] if author else [],
                    "language": parser.language,
                    "outbound_links": [urljoin(current, href) for href in parser.links
                                       if urljoin(current, href).startswith(("http://", "https://"))],
                    "date_origin": origin, "date_confidence": confidence,
                }
                text = _html_text(raw, parser)
                is_text = True
            elif is_pdf:
                text = _pdf_text(raw)
                published, modified, origin, confidence = _date_metadata(parser, current, exa_published_date)
                resolved_url = current
                metadata.update({"date_origin": origin, "date_confidence": confidence})
                is_text = True
            else:
                published = modified = None
                resolved_url = current
                is_text = False
        except Exception as exc:
            return self._retry(key, url, f"document parsing failed: {exc}")

        if is_html and text is not None and len(text) < 1000:
            barrier_text = f"{metadata.get('title') or ''}\n{text}"
            if BLOCKED_PAGE.search(barrier_text) or parser.has_password_input:
                self._record_status(key, url, "blocked", "login or CAPTCHA page")
                self._document_stub(original_id, url, registry_row, "blocked")
                return {"url": url, "document_id": original_id, "fetch_status": "blocked",
                        "http_status": response_status, "snapshot_path": None}

        data_for_hash = text.encode("utf-8") if is_text else raw
        content_sha = hashlib.sha256(data_for_hash).hexdigest()
        simhash = _simhash(text) if text and text.strip() else None
        resolved_id = source_id(resolved_url)
        targets = [original_id] + ([resolved_id] if resolved_id != original_id else [])
        fetched_registry = lookup(self.conn, current)
        effective_registry = dict(fetched_registry) if fetched_registry else None
        if effective_registry:
            effective_registry["can_store_full_text"] = bool(
                registry_row and registry_row["can_store_full_text"]
                and fetched_registry["can_store_full_text"]
            )
        with self.conn.transaction():
            for document_id in targets:
                self._document(document_id, url if document_id == original_id else resolved_url,
                               resolved_url, effective_registry, metadata, "fetched", published, modified)
            if resolved_id != original_id:
                edge_id = "same-as:" + short_hash("|".join(sorted((original_id, resolved_id))), 24)
                self.conn.execute(
                    "INSERT INTO ev_kg_edge(edge_id,type,src,dst,payload,valid_from,created_at) "
                    "VALUES (%s,'SAME_AS',%s,%s,'{}',now(),now()) ON CONFLICT (edge_id) DO NOTHING",
                    (edge_id, original_id, resolved_id),
                )
            snapshots = {}
            for document_id in targets:
                can_store = bool(effective_registry and effective_registry["can_store_full_text"])
                snapshot, _ = self._store_version(document_id, text, raw, content_sha, simhash, response_status,
                                                  can_store, is_text)
                snapshots[document_id] = snapshot
            self._record_status(key, url, "fetched")
        return {"url": url, "document_id": original_id, "resolved_document_id": resolved_id,
                "fetch_status": "fetched", "http_status": response_status, "date_origin": metadata["date_origin"],
                "snapshot_path": snapshots.get(original_id), "snapshot_stored": bool(snapshots.get(original_id)),
                "content_sha256": content_sha}

    def fetch_queue(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT url,url_hash FROM ev_fetch_queue WHERE status='queued' AND next_attempt_at <= now() "
            "ORDER BY priority DESC,enqueued_at LIMIT %s", (limit,)
        ).fetchall()
        return [self.fetch(row["url"]) for row in rows]
