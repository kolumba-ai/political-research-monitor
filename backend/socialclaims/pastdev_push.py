"""past.dev claim memory writer: sends each post's current claims per politician from Postgres to past.dev.

One data point per post x politician, `sc-<oriane_id>-<person slug>`, timed at the post's `published_at` and scoped to
the politician's audience (`pol-<slug>`, a rule over the reader identity's `politician` trait). The text uses the same
`<headline>\\n- claims\\n\\nJSON:\\n<payload>` shape that `pull-pastdev` reads. Claims come from the latest successful
extraction of each post x politician; `grounding = 'failed'` claims are never sent. Every sent id is recorded in
`memory_record`, and unchanged data points are skipped, so re-runs only send new or changed claims.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from urllib.parse import quote

import structlog

from .pastdev import PastDevClient, PastDevError, post_url

log = structlog.get_logger(__name__)

PAYLOAD_FORMAT = "socialclaims-post/1"
CREDIT_BYTES = 350
MAX_BATCH_ITEMS = 1000
MAX_BATCH_BYTES = 8 * 1024 * 1024
CAPTION_CHARS = 1000

CLAIMS_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (ce.oriane_id, ce.politician_id) ce.oriane_id, ce.politician_id, ce.raw_id, ce.extractor_version
    FROM claim_extraction ce
    WHERE ce.status = 'ok' AND split_part(ce.politician_id, ':', 2) = %(country)s
      AND (%(ids)s::text[] IS NULL OR ce.politician_id = ANY(%(ids)s))
      AND (%(version)s::text IS NULL OR ce.extractor_version = %(version)s)
    ORDER BY ce.oriane_id, ce.politician_id, ce.extracted_at DESC
)
SELECT c.*, p.platform, p.platform_id, p.format, p.url, p.profile_handle, p.caption, p.published_at,
       r.name AS politician_name, m.views, m.likes, m.shares, m.comments, m.captured_at AS metrics_captured_at
FROM latest l
JOIN distilled_claim c ON c.oriane_id = l.oriane_id AND c.politician_id = l.politician_id
                      AND c.raw_id = l.raw_id AND c.extractor_version = l.extractor_version
JOIN post p ON p.oriane_id = c.oriane_id
LEFT JOIN politician_ref r ON r.politician_id = c.politician_id AND r.superseded_at IS NULL
LEFT JOIN LATERAL (SELECT views, likes, shares, comments, captured_at FROM post_metrics pm
                   WHERE pm.oriane_id = p.oriane_id ORDER BY captured_at DESC LIMIT 1) m ON TRUE
WHERE c.grounding = ANY(%(grounding)s)
ORDER BY c.politician_id, c.oriane_id, c.quote_char_start NULLS LAST, c.claim_id
"""


def person_slug(politician_id: str) -> str:
    return politician_id.split(":")[-1]


def audience_slug(politician_id: str) -> str:
    """`pol-<person slug>`, shortened with a stable hash suffix to past.dev's 40-character slug limit."""
    slug = f"pol-{person_slug(politician_id)}"
    if len(slug) <= 40:
        return slug
    digest = hashlib.sha256(politician_id.encode()).hexdigest()[:6]
    return f"{slug[:33].rstrip('-')}-{digest}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _speaker(c: dict) -> str:
    if c["relation_to_politician"] == "speaker" and c["speaker_id"] == c["politician_id"]:
        return "politician"
    if c["relation_to_politician"] == "quoted" or c["modality"] == "quoted":
        return "quoted"
    return "other" if c["speaker_id"] else "unknown"


def credits_for(content: str) -> int:
    return max(1, math.ceil(len(content.encode()) / CREDIT_BYTES))


def build_item(claims: list[dict], country: str) -> dict:
    """One past.dev data point for one post x politician; `claims` share oriane_id and politician_id."""
    first = claims[0]
    name = first["politician_name"] or person_slug(first["politician_id"])
    handle = first["profile_handle"] or "unknown"
    published = _iso(first["published_at"])
    source = {"oriane_content_id": first["oriane_id"], "platform": first["platform"],
              "platform_id": first["platform_id"], "format": first["format"], "uploader_handle": first["profile_handle"],
              "published_at": published, "caption": (first["caption"] or "")[:CAPTION_CHARS] or None}
    source["url"] = first["url"] or post_url(source)
    payload = {
        "format": PAYLOAD_FORMAT,
        "politician": {"name": name, "system_a_person_id": first["politician_id"], "country": country},
        "source": source,
        "engagement_snapshot": {k: first[k] for k in ("views", "likes", "shares", "comments") if first[k] is not None}
        | ({"captured_at": _iso(first["metrics_captured_at"])} if first["metrics_captured_at"] else {}),
        "claims": [{
            "claim_index": i, "claim_id": c["claim_id"], "claim_es": c["text"], "claim_en": c["text_en"],
            "claim_type": c["type"], "speaker": _speaker(c), "speaker_id": c["speaker_id"],
            "speaker_status": c["speaker_status"], "relation_to_politician": c["relation_to_politician"],
            "about_politician": c["relation_to_politician"] in ("speaker", "subject"),
            "checkable": c["checkworthiness"] >= 0.5, "checkworthiness": round(c["checkworthiness"], 3),
            "topics": list(c["topics"] or []), "modality": c["modality"], "grounding": c["grounding"],
            "evidence_span": c["quote"], "quote_start_s": c["quote_start_s"], "quote_end_s": c["quote_end_s"],
            "proposition_id": c["proposition_id"],
        } for i, c in enumerate(claims)],
        "timestamps": {"published_at": published, "claims_extracted_at": _iso(max(c["extracted_at"] for c in claims))},
        "extractor_version": first["extractor_version"],
    }
    headline = (f"{first['platform']} post by @{handle} on {(published or '')[:10]} mentioning {name}. "
                f"{len(claims)} claim(s):")
    bullets = "".join(f"- {c['text']}\n" for c in claims)
    content = f"{headline}\n{bullets}\nJSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    item = {
        "id": f"sc-{first['oriane_id']}-{person_slug(first['politician_id'])}",
        "content": content,
        "label": f"{first['platform']} post by @{handle} about {name}",
        "timestamp": published,
        "audience": audience_slug(first["politician_id"]),
        "metadata": {"kind": "post_claims", "oriane_id": first["oriane_id"], "politician_id": first["politician_id"],
                     "claim_ids": [c["claim_id"] for c in claims], "extractor_version": first["extractor_version"]},
    }
    return item


def item_sha256(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def collect(conn, country: str, politician_ids: list[str] | None = None, extractor_version: str | None = None,
            grounding: tuple[str, ...] = ("exact", "review")) -> list[dict]:
    rows = conn.execute(CLAIMS_SQL, {"country": country, "ids": politician_ids or None, "version": extractor_version,
                                     "grounding": list(grounding)}).fetchall()
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["oriane_id"], r["politician_id"]), []).append(r)
    items = [build_item(g, country) for g in groups.values()]
    return sorted(items, key=lambda i: (i["timestamp"] or "", i["id"]))


def _batches(items: list[dict]) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for it in items:
        n = len(it["content"].encode())
        if cur and (len(cur) >= MAX_BATCH_ITEMS or size + n > MAX_BATCH_BYTES):
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    if cur:
        out.append(cur)
    return out


def ensure_audiences(client: PastDevClient, items: list[dict], country: str) -> dict[str, str]:
    """Map each politician to its audience slug, reusing an existing audience that reaches a reader identity whose
    `politician` trait names them, and creating the reader identity and rule audience otherwise."""
    reader_of = {i["identity"]: (i.get("traits") or {}).get("politician") for i in client.identities()}
    existing = client.audiences()
    reuse = {}
    for a in existing:
        for ident in a.get("identities") or []:
            if reader_of.get(ident):
                reuse.setdefault(reader_of[ident], a["slug"])
    slugs = {a["slug"] for a in existing}
    mapping: dict[str, str] = {}
    created = []
    for pid in sorted({i["metadata"]["politician_id"] for i in items}):
        if pid in reuse:
            mapping[pid] = reuse[pid]
            continue
        slug = audience_slug(pid)
        mapping[pid] = slug
        if slug in slugs:
            continue
        reader = f"reader-{slug}"
        client.put(f"/identities/{quote(reader, safe='')}",
                   {"traits": {"role": "politician-reader", "country": country, "politician": pid}})
        name = next(i for i in items if i["metadata"]["politician_id"] == pid)["label"].split(" about ", 1)[-1]
        client.put(f"/audiences/{slug}", {
            "kind": "rule", "name": f"{name} ({country}) - social claims"[:200],
            "rule": {"match": "any", "conditions": [
                {"trait": "politician", "operator": "is", "values": [pid]},
                {"trait": "scope", "operator": "is", "values": ["country"]}]}})
        slugs.add(slug)
        created.append(slug)
    if created:
        log.info("pastdev.audiences_created", slugs=created)
    return mapping


def push(conn, client: PastDevClient | None, country: str, *, politician_ids: list[str] | None = None,
         extractor_version: str | None = None, dry_run: bool = False, max_credits: int | None = None,
         poll_timeout_s: float = 900.0, poll_interval_s: float = 2.0, sleep=time.sleep, now=None) -> dict:
    country = country.upper()
    now = now or (lambda: datetime.now(timezone.utc))
    project = f"claims-{country.lower()}"
    items = collect(conn, country, politician_ids, extractor_version)
    sent = {r["datapoint_id"]: r for r in conn.execute(
        "SELECT datapoint_id, content_sha256, status, audience FROM memory_record").fetchall()}

    def unchanged(it: dict) -> bool:
        rec = sent.get(it["id"])
        return bool(rec and rec["status"] != "failed" and rec["content_sha256"] == item_sha256(it))

    if client is not None and not dry_run:
        mapping = ensure_audiences(client, items, country)
        for it in items:
            it["audience"] = mapping[it["metadata"]["politician_id"]]
    todo = [it for it in items if not unchanged(it)]
    current_ids = {it["id"] for it in items}
    summary = {
        "country": country, "project": project, "data_points": len(items),
        "claims": sum(len(it["metadata"]["claim_ids"]) for it in items),
        "politicians": len({it["metadata"]["politician_id"] for it in items}),
        "already_sent": len(items) - len(todo), "to_send": len(todo),
        "to_send_claims": sum(len(it["metadata"]["claim_ids"]) for it in todo),
        "bytes": sum(len(it["content"].encode()) for it in todo),
        "estimated_credits": sum(credits_for(it["content"]) for it in todo),
        "stale_records": sorted(set(sent) - current_ids)[:50],
        "dry_run": dry_run,
    }
    if dry_run or not todo:
        return summary
    if max_credits is not None and summary["estimated_credits"] > max_credits:
        summary["error"] = f"estimated {summary['estimated_credits']} credits exceeds --max-credits {max_credits}"
        return summary

    ingestions, errors, sent_count = [], [], 0
    for batch in _batches(todo):
        key = "sc-push-" + hashlib.sha256("".join(it["id"] + item_sha256(it) for it in batch).encode()).hexdigest()[:40]
        try:
            resp = client.post("/ingest/batch", {"idempotencyKey": key, "items": batch})
        except PastDevError as exc:
            errors.append(str(exc)[:300])
            break
        ingestion_id = resp.get("ingestionId")
        by_ordinal = {i.get("ordinal"): i for i in resp.get("items") or []}
        at = now()
        for n, it in enumerate(batch):
            done = bool((by_ordinal.get(n) or {}).get("unchanged")) or resp.get("unchanged") is True
            _record(conn, it, project, ingestion_id, "completed" if done else "queued", at, at if done else None)
        ingestions.append(ingestion_id)
        sent_count += len(batch)
        log.info("pastdev.batch_sent", ingestion_id=ingestion_id, items=len(batch))

    statuses = {}
    for ingestion_id in ingestions:
        statuses[ingestion_id] = _wait(client, ingestion_id, poll_timeout_s, poll_interval_s, sleep)
        st = statuses[ingestion_id]
        if st["status"] in ("completed", "failed"):
            conn.execute("UPDATE memory_record SET status = %s, completed_at = %s, error = %s "
                         "WHERE ingestion_id = %s AND status = 'queued'",
                         (st["status"], now() if st["status"] == "completed" else None,
                          json.dumps(st.get("failure")) if st.get("failure") else None, ingestion_id))
    summary.update({"batches": len(ingestions), "sent": sent_count, "ingestions": statuses, "errors": errors})
    return summary


def _record(conn, it: dict, project: str, ingestion_id: str | None, status: str, sent_at, completed_at) -> None:
    md = it["metadata"]
    conn.execute("""
        INSERT INTO memory_record (datapoint_id, kind, oriane_id, politician_id, claim_ids, extractor_version,
            past_project, audience, ingestion_id, status, content_sha256, content_bytes, memory_timestamp, sent_at,
            completed_at)
        VALUES (%s, 'post_claims', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (datapoint_id) DO UPDATE SET claim_ids = EXCLUDED.claim_ids,
            extractor_version = EXCLUDED.extractor_version, audience = EXCLUDED.audience,
            ingestion_id = EXCLUDED.ingestion_id, status = EXCLUDED.status, content_sha256 = EXCLUDED.content_sha256,
            content_bytes = EXCLUDED.content_bytes, memory_timestamp = EXCLUDED.memory_timestamp,
            sent_at = EXCLUDED.sent_at, completed_at = EXCLUDED.completed_at, error = NULL""",
        (it["id"], md["oriane_id"], md["politician_id"], md["claim_ids"], md["extractor_version"], project,
         it["audience"], ingestion_id, status, item_sha256(it), len(it["content"].encode()), it["timestamp"],
         sent_at, completed_at))


def _wait(client: PastDevClient, ingestion_id: str, timeout_s: float, interval_s: float, sleep) -> dict:
    waited = 0.0
    while True:
        st = client.get(f"/ingest/{ingestion_id}")
        if st.get("status") in ("completed", "failed") or waited >= timeout_s:
            return {"status": st.get("status"), "failure": st.get("failure")}
        sleep(interval_s)
        waited += interval_s
