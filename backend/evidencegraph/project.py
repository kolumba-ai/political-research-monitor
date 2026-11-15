"""past.dev projection of enriched claims: the claim, the documents found for it and one stance link per document.

Written only when `enrich` has fetched and judged evidence for a proposition, never as a bulk copy of Postgres.
Records (each `<plain text>\\n\\nJSON:\\n<compact payload>`):

- claim `clm-<proposition hash>-<person slug>`, timed at `stated_at`, in the politician's audience;
- document `doc-<source hash>-v<n>`, timed at `published_at` (retrieval time when unknown), project-visible;
- evidence link `lnk-<link hash>-<person slug>`, timed at `assessed_at`, in the politician's audience.

Only links with a verified quote and a stance other than `irrelevant` are written, and only their documents; a claim
without any such link is not written at all.
Quotes follow the storage policy: open licences keep the quote, `copyright` at most 300 characters, `unknown` 200.
Every id goes to `ev_memory_record`; unchanged items are not resent.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from urllib.parse import quote

import structlog

from polgraph.ids import short_hash
from socialclaims.pastdev import PastDevClient, PastDevError

log = structlog.get_logger(__name__)

CREDIT_BYTES = 350
MAX_BATCH_ITEMS = 1000
MAX_BATCH_BYTES = 8 * 1024 * 1024
QUOTE_LIMIT = {"open_gov": None, "open_access": None, "copyright": 300, "unknown": 200}
PAYLOAD_FORMAT = {"claim": "evidencegraph-claim/1", "document": "evidencegraph-document/1",
                  "evidence_link": "evidencegraph-link/1"}


def person_slug(politician_id: str) -> str:
    return politician_id.split(":")[-1]


def audience_slug(politician_id: str) -> str:
    """`pol-<person slug>`, shortened with a stable hash suffix to past.dev's 40-character slug limit."""
    slug = f"pol-{person_slug(politician_id)}"
    if len(slug) <= 40:
        return slug
    return f"{slug[:33].rstrip('-')}-{hashlib.sha256(politician_id.encode()).hexdigest()[:6]}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def credits_for(content: str) -> int:
    return max(1, math.ceil(len(content.encode()) / CREDIT_BYTES))


def item_sha256(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _content(text: str, payload: dict) -> str:
    return f"{text}\n\nJSON:\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True)}"


def allowed_quote(quote_text: str | None, licence: str) -> str | None:
    if not quote_text:
        return None
    limit = QUOTE_LIMIT.get(licence, QUOTE_LIMIT["unknown"]) if licence != "licensed" else 0
    return quote_text if limit is None or len(quote_text) <= limit else None


def claim_items(prop: dict, claims: list[dict]) -> list[dict]:
    items = []
    for pid in sorted({c["politician_id"] for c in claims}):
        own = [c for c in claims if c["politician_id"] == pid]
        name = own[0]["politician_name"] or person_slug(pid)
        stated = min(c["stated_at"] for c in own)
        lines = [f"CLAIM by or about {name} ({_iso(stated)[:10]}): {prop['canonical_text']}"]
        lines += [f"- \"{c['quote']}\" ({c['platform'] or 'post'} {c['url'] or c['oriane_id']}, {_iso(c['stated_at'])[:10]})"
                  for c in own[:5]]
        payload = {
            "format": PAYLOAD_FORMAT["claim"], "proposition_id": prop["proposition_id"],
            "canonical_text": prop["canonical_text"], "politician": {"name": name, "system_a_person_id": pid},
            "claims": [{"claim_id": c["claim_id"], "text": c["text"], "text_en": c["text_en"], "type": c["type"],
                        "quote": c["quote"], "relation_to_politician": c["relation_to_politician"], "platform": c["platform"],
                        "oriane_id": c["oriane_id"], "url": c["url"], "stated_at": _iso(c["stated_at"])}
                       for c in own],
        }
        items.append({
            "id": f"clm-{short_hash(prop['proposition_id'], 16)}-{person_slug(pid)}",
            "label": f"claim {prop['proposition_id']} about {name}"[:200],
            "content": _content("\n".join(lines), payload), "timestamp": _iso(stated),
            "metadata": {"kind": "claim", "proposition_id": prop["proposition_id"], "politician_id": pid},
        })
    return items


def document_item(doc: dict, link: dict) -> dict:
    when = doc["published_at"] or doc["retrieved_at"]
    publisher = doc["publisher"] or doc["publisher_domain"]
    text = (f"DOCUMENT by {publisher} ({doc['source_class'] or 'unregistered'}, tier {doc['authority_tier']}) "
            f"published {_iso(doc['published_at'])[:10] if doc['published_at'] else 'on an unknown date'}: "
            f"{doc['title'] or doc['resolved_url']}. {doc['resolved_url']}\n{link['summary']}")
    payload = {"format": PAYLOAD_FORMAT["document"], "document_id": doc["document_id"], "url": doc["resolved_url"],
               "publisher": publisher, "source_class": doc["source_class"], "authority_tier": doc["authority_tier"],
               "licence_class": doc["licence_class"], "title": doc["title"], "published_at": _iso(doc["published_at"]),
               "date_confidence": doc["date_confidence"], "retrieved_at": _iso(doc["retrieved_at"]),
               "date_is_retrieval": doc["published_at"] is None, "content_sha256": doc["content_sha256"],
               "summary": link["summary"]}
    return {"id": f"doc-{doc['document_id'].split(':', 1)[-1]}-v{doc['version']}",
            "label": f"document {publisher}: {doc['title'] or doc['resolved_url']}"[:200],
            "content": _content(text, payload), "timestamp": _iso(when),
            "metadata": {"kind": "document", "document_id": doc["document_id"]}}


def link_items(prop: dict, link: dict, doc: dict, names: dict[str, str]) -> list[dict]:
    publisher = doc["publisher"] or doc["publisher_domain"]
    q = allowed_quote(link["quote"], doc["licence_class"])
    items = []
    for pid in prop["politician_ids"]:
        name = names.get(pid) or person_slug(pid)
        text = (f"EVIDENCE: {publisher} ({_iso(doc['published_at'])[:10] if doc['published_at'] else 'undated'}) "
                f"{link['stance'].upper()} the claim by or about {name}: \"{prop['canonical_text']}\". "
                f"Reason: {link['rationale']}" + (f" Quote: \"{q}\"" if q else "") + f" Source: {doc['resolved_url']}")
        payload = {"format": PAYLOAD_FORMAT["evidence_link"], "link_id": link["link_id"],
                   "proposition_id": prop["proposition_id"], "document_id": doc["document_id"],
                   "document_datapoint": f"doc-{doc['document_id'].split(':', 1)[-1]}-v{doc['version']}",
                   "stance": link["stance"], "confidence": round(link["confidence"], 3), "quote": q,
                   "rationale": link["rationale"], "source_class": doc["source_class"],
                   "authority_tier": doc["authority_tier"], "assessed_at": _iso(link["assessed_at"]),
                   "mapper_version": link["mapper_version"], "politician": {"name": name, "system_a_person_id": pid}}
        items.append({"id": f"lnk-{short_hash(link['link_id'], 16)}-{person_slug(pid)}",
                      "label": f"evidence {link['stance']} {prop['proposition_id']} about {name}"[:200],
                      "content": _content(text, payload), "timestamp": _iso(link["assessed_at"]),
                      "metadata": {"kind": "evidence_link", "proposition_id": prop["proposition_id"],
                                   "document_id": doc["document_id"], "politician_id": pid}})
    return items


LINKS_SQL = """
SELECT l.*, d.resolved_url, d.publisher_domain, d.source_class, d.authority_tier, d.licence_class, d.title,
       d.published_at, d.date_confidence, r.publisher, v.retrieved_at,
       (SELECT count(*) FROM ev_document_version v2 WHERE v2.document_id = l.document_id
        AND v2.retrieved_at <= v.retrieved_at) AS version
FROM ev_evidence_link l
JOIN ev_document d ON d.document_id = l.document_id
JOIN ev_document_version v ON v.document_id = l.document_id AND v.content_sha256 = l.content_sha256
LEFT JOIN ev_source_registry r ON r.domain = d.publisher_domain
WHERE l.proposition_id = %s AND l.mapper_version = %s AND l.quote_verified AND l.stance <> 'irrelevant'
  AND l.content_sha256 = (SELECT v3.content_sha256 FROM ev_document_version v3 WHERE v3.document_id = l.document_id
                          ORDER BY v3.retrieved_at DESC LIMIT 1)
ORDER BY l.assessed_at, l.link_id
"""


def build_items(conn, prop: dict, claims: list[dict], mapper_version: str) -> list[dict]:
    names = {c["politician_id"]: c["politician_name"] for c in claims if c["politician_name"]}
    docs: dict[str, dict] = {}
    links: list[dict] = []
    for row in conn.execute(LINKS_SQL, (prop["proposition_id"], mapper_version)).fetchall():
        item = document_item(row, row)
        docs.setdefault(item["id"], item)
        links += link_items(prop, row, row, names)
    if not links:
        return []
    return claim_items(prop, claims) + list(docs.values()) + links


def resolve_audiences(client: PastDevClient | None, pids: list[str], recorded: dict[str, str]) -> dict[str, dict]:
    """Per politician: the audience to use and whether it must be created (read-only).

    An existing audience is reused only when every identity it lists reads that same politician."""
    if client is None:
        return {pid: {"slug": recorded.get(pid) or audience_slug(pid), "create": False} for pid in pids}
    reader_of = {i["identity"]: (i.get("traits") or {}).get("politician") for i in client.identities()}
    existing = client.audiences()
    reuse: dict[str, str] = {}
    for a in existing:
        members = a.get("identities") or []
        owners = {reader_of.get(ident) for ident in members}
        if members and len(owners) == 1 and None not in owners:
            reuse.setdefault(owners.pop(), a["slug"])
    slugs = {a["slug"] for a in existing}
    return {pid: {"slug": reuse[pid], "create": False} if pid in reuse
            else {"slug": audience_slug(pid), "create": audience_slug(pid) not in slugs} for pid in pids}


def create_audiences(client: PastDevClient, plan: dict[str, dict], names: dict[str, str], country: str) -> list[str]:
    created = []
    for pid, aud in plan.items():
        if not aud["create"]:
            continue
        slug = aud["slug"]
        client.put(f"/identities/{quote(f'reader-{slug}', safe='')}",
                   {"traits": {"role": "politician-reader", "country": country, "politician": pid}})
        client.put(f"/audiences/{slug}", {
            "kind": "rule", "name": f"{names.get(pid) or person_slug(pid)} ({country}) - claims and evidence"[:200],
            "rule": {"match": "any", "conditions": [
                {"trait": "politician", "operator": "is", "values": [pid]},
                {"trait": "scope", "operator": "is", "values": ["country"]}]}})
        aud["create"] = False
        created.append(slug)
    return created


def _batches(items: list[dict]):
    batch, size = [], 0
    for it in items:
        n = len(json.dumps(it, ensure_ascii=False).encode())
        if batch and (len(batch) >= MAX_BATCH_ITEMS or size + n > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(it)
        size += n
    if batch:
        yield batch


def _settle(conn, project: str, ingestion_id: str, st: dict, now) -> None:
    if st.get("status") in ("completed", "failed"):
        conn.execute("UPDATE ev_memory_record SET status = %s, completed_at = %s, error = %s "
                     "WHERE past_project = %s AND ingestion_id = %s AND status = 'queued'",
                     (st["status"], now() if st["status"] == "completed" else None,
                      json.dumps(st.get("failure")) if st.get("failure") else None, project, ingestion_id))


def refresh_queued(conn, client: PastDevClient, project: str, now) -> int:
    """Re-check this project's ingestions an earlier run left `queued`, so failed items are resent."""
    ids = [r["ingestion_id"] for r in conn.execute(
        "SELECT DISTINCT ingestion_id FROM ev_memory_record WHERE status = 'queued' AND ingestion_id IS NOT NULL "
        "AND past_project = %s", (project,))]
    for ingestion_id in ids:
        _settle(conn, project, ingestion_id, client.get(f"/ingest/{ingestion_id}"), now)
    return len(ids)


def _wait(client: PastDevClient, ingestion_id: str, timeout_s: float, interval_s: float, sleep) -> dict:
    waited = 0.0
    while True:
        st = client.get(f"/ingest/{ingestion_id}")
        if st.get("status") in ("completed", "failed") or waited >= timeout_s:
            return {"status": st.get("status"), "failure": st.get("failure")}
        sleep(interval_s)
        waited += interval_s


def _record(conn, it: dict, project: str, ingestion_id: str | None, status: str, at, completed_at) -> None:
    md = it["metadata"]
    conn.execute("""
        INSERT INTO ev_memory_record (datapoint_id, kind, proposition_id, document_id, politician_id, past_project,
            audience, ingestion_id, status, content_sha256, content_bytes, memory_timestamp, sent_at, completed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (datapoint_id) DO UPDATE SET audience = EXCLUDED.audience, ingestion_id = EXCLUDED.ingestion_id,
            status = EXCLUDED.status, content_sha256 = EXCLUDED.content_sha256,
            content_bytes = EXCLUDED.content_bytes, memory_timestamp = EXCLUDED.memory_timestamp,
            sent_at = EXCLUDED.sent_at, completed_at = EXCLUDED.completed_at, error = NULL""",
        (it["id"], md["kind"], md.get("proposition_id"), md.get("document_id"), md.get("politician_id"), project,
         it.get("audience"), ingestion_id, status, item_sha256(it), len(it["content"].encode()), it["timestamp"],
         at, completed_at))


def project(conn, client: PastDevClient, prop: dict, claims: list[dict], *, country: str, mapper_version: str,
            poll_timeout_s: float = 300.0, poll_interval_s: float = 2.0, sleep=time.sleep, now=None) -> dict:
    """Send one enriched proposition's claim, documents and links; returns what was sent."""
    now = now or (lambda: datetime.now(timezone.utc))
    past_project = f"claims-{country.lower()}"
    items = build_items(conn, prop, claims, mapper_version)
    if not items:
        return {"items": 0, "sent": 0, "unchanged": 0, "credits_estimate": 0, "errors": [], "pending": 0}
    refresh_queued(conn, client, past_project, now)
    pids = sorted({it["metadata"]["politician_id"] for it in items if it["metadata"].get("politician_id")})
    recorded = {r["politician_id"]: r["audience"] for r in conn.execute(
        "SELECT DISTINCT politician_id, audience FROM ev_memory_record WHERE past_project = %s "
        "AND politician_id = ANY(%s) AND audience IS NOT NULL", (past_project, pids))}
    plan = resolve_audiences(client, pids, recorded)
    for it in items:
        pid = it["metadata"].get("politician_id")
        if pid:
            it["audience"] = plan[pid]["slug"]
    sent = {r["datapoint_id"]: r for r in conn.execute(
        "SELECT datapoint_id, content_sha256, status FROM ev_memory_record WHERE datapoint_id = ANY(%s)",
        ([it["id"] for it in items],))}
    todo = [it for it in items if not (sent.get(it["id"]) and sent[it["id"]]["status"] != "failed"
                                       and sent[it["id"]]["content_sha256"] == item_sha256(it))]
    summary = {"items": len(items), "sent": 0, "unchanged": len(items) - len(todo),
               "credits_estimate": sum(credits_for(it["content"]) for it in todo), "errors": []}
    if not todo:
        summary["pending"] = _pending(conn, items, 0)
        return summary
    names = {c["politician_id"]: c["politician_name"] for c in claims if c["politician_name"]}
    summary["audiences_created"] = create_audiences(client, plan, names, country)
    ingestions = []
    for batch in _batches(todo):
        key = "ev-enrich-" + hashlib.sha256("".join(it["id"] + item_sha256(it) for it in batch).encode()).hexdigest()[:40]
        try:
            resp = client.post("/ingest/batch", {"idempotencyKey": key, "items": batch})
        except PastDevError as exc:
            summary["errors"].append(str(exc)[:300])
            break
        ingestion_id = resp.get("ingestionId")
        by_ordinal = {i.get("ordinal"): i for i in resp.get("items") or []}
        at = now()
        for n, it in enumerate(batch):
            done = bool((by_ordinal.get(n) or {}).get("unchanged")) or resp.get("unchanged") is True
            _record(conn, it, past_project, ingestion_id, "completed" if done else "queued", at, at if done else None)
        ingestions.append(ingestion_id)
        summary["sent"] += len(batch)
    for ingestion_id in ingestions:
        _settle(conn, past_project, ingestion_id, _wait(client, ingestion_id, poll_timeout_s, poll_interval_s, sleep), now)
    summary["ingestions"] = ingestions
    summary["pending"] = _pending(conn, items, len(todo) - summary["sent"])
    return summary


def _pending(conn, items: list[dict], unsent: int) -> int:
    """Items not yet completed in past.dev: recorded as queued/failed, or never sent."""
    return conn.execute(
        "SELECT count(*) AS n FROM ev_memory_record WHERE datapoint_id = ANY(%s) AND status <> 'completed'",
        ([it["id"] for it in items],)).fetchone()["n"] + unsent
