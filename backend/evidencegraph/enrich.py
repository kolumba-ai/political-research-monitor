"""Claim enrichment: for each queued System B proposition, search Exa, fetch the pages from their publishers, have
Claude summarise each page and judge whether it supports or contradicts the claim, then write the claim, documents
and stance links to past.dev (see `project.py`).

Exa is used only to find URLs: requests ask for no text or highlights and only URL, title, date, author and score
are kept. Summaries and stances are judged on the page text fetched by `Fetcher`, held in memory; it is stored only
for licences that allow full text. A stance whose quote is not found in the fetched text is kept in Postgres with
`quote_verified = false` and never projected.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

import structlog

from polgraph.ids import canonical_url, normalize_text, short_hash

from . import project as projection

log = structlog.get_logger(__name__)

SOCIAL_DOMAINS = ["tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "youtube.com"]
# Query-plan rows (design doc, "Claim-driven discovery"): registry classes restrict `includeDomains`.
ROWS = {
    "neutral": {"category": "news", "location": "MX"},
    "counter": {"location": "MX"},
    "primary": {"classes": ["government_publication", "legislative_judicial"], "location": "MX"},
    "indicator": {"classes": ["official_statistics"], "location": "MX"},
    "fact_check": {"classes": ["fact_check"], "location": "MX"},
    "expert": {"classes": ["think_tank_ngo", "academic"], "location": "MX"},
    "english": {},
}
STANCES = ["supports", "contradicts", "context", "irrelevant"]
EXA_SEARCH_USD = 0.007
# Rough per-call token use for estimates; the price per million tokens is an assumption, check the real rate.
PLAN_TOKENS = (1_500, 600)
ASSESS_OVERHEAD_TOKENS = (1_500, 500)
CHARS_PER_TOKEN = 3.5
USD_PER_MTOK = (3.0, 15.0)

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["queries"],
    "properties": {"queries": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["row", "query", "skip", "skip_reason"],
        "properties": {"row": {"type": "string", "enum": list(ROWS)}, "query": {"type": "string"},
                       "skip": {"type": "boolean"}, "skip_reason": {"type": "string"}}}}},
}
ASSESS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "stance", "confidence", "quote", "rationale"],
    "properties": {"summary": {"type": "string"}, "stance": {"type": "string", "enum": STANCES},
                   "confidence": {"type": "number"}, "quote": {"type": "string"}, "rationale": {"type": "string"}},
}
PLAN_SYSTEM = """You plan web searches that find evidence for or against a claim made in Mexican political social media.
Write one search query per row, in Spanish unless the row is `english`:
- neutral: neutral restatement of the claim, as news coverage would phrase it;
- counter: explicitly the opposite outcome (e.g. "aumentaron" for a claimed decrease);
- primary: the official record of the act or figure (decree, bulletin, session record);
- indicator: only for statistics; the dataset or bulletin of the indicator, not the claim wording; otherwise skip;
- fact_check: the same or a similar claim checked before;
- expert: only for policies, accusations or statistics; topic-level analysis; otherwise skip;
- english: international coverage in English; skip unless the claim concerns national-level matters.
Queries are short keyword phrases with place and period. Set skip=true with a reason for rows that do not apply."""
ASSESS_SYSTEM = """You judge one web page against one claim. Use only the page text given.
- summary: 1-3 sentences in Spanish, in your own words, stating the facts the page reports that bear on the claim's
  subject (never copy sentences).
- stance: supports (the page confirms the claim), contradicts (it states the opposite or a clearly different value),
  context (true but missing a qualification, scope or period that changes its meaning), irrelevant (does not address it).
- quote: the single sentence of the page the stance rests on, copied exactly, at most 300 characters; empty if irrelevant.
- rationale: one sentence in English explaining the stance.
- confidence: 0 to 1."""


def _usd(tokens_in: float, tokens_out: float) -> float:
    return tokens_in / 1e6 * USD_PER_MTOK[0] + tokens_out / 1e6 * USD_PER_MTOK[1]


def estimate(n_props: int, max_docs: int, page_chars: int) -> dict:
    searches = n_props * len(ROWS)
    assess_in = page_chars / CHARS_PER_TOKEN + ASSESS_OVERHEAD_TOKENS[0]
    llm = n_props * (_usd(*PLAN_TOKENS) + max_docs * _usd(assess_in, ASSESS_OVERHEAD_TOKENS[1]))
    return {"max_exa_searches": searches, "max_exa_usd": round(searches * EXA_SEARCH_USD, 2),
            "max_llm_calls": n_props * (1 + max_docs), "max_llm_usd": round(llm, 2),
            "max_total_usd": round(searches * EXA_SEARCH_USD + llm, 2),
            "assumed_usd_per_mtok_in_out": list(USD_PER_MTOK)}


CLAIMS_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (oriane_id, politician_id) oriane_id, politician_id, raw_id, extractor_version
    FROM claim_extraction WHERE status = 'ok'
    ORDER BY oriane_id, politician_id, extracted_at DESC
)
SELECT d.claim_id, d.politician_id, d.oriane_id, d.quote, d.text, d.text_en, d.type, d.relation_to_politician,
       d.stated_at, p.platform, p.url, r.name AS politician_name
FROM distilled_claim d
JOIN latest l ON l.oriane_id = d.oriane_id AND l.politician_id = d.politician_id
             AND l.raw_id = d.raw_id AND l.extractor_version = d.extractor_version
JOIN post p ON p.oriane_id = d.oriane_id
LEFT JOIN politician_ref r ON r.politician_id = d.politician_id AND r.superseded_at IS NULL
WHERE d.proposition_id = %s AND d.grounding = 'exact' AND d.modality IN ('asserted', 'quoted')
ORDER BY d.stated_at, d.claim_id
"""


# A proposition left `mapping` longer than this (worker killed mid-run) is available again.
MAPPING_LEASE_MINUTES = 60
READY = ("(q.status = 'queued' OR (q.status = 'mapping' AND q.updated_at < now() - make_interval(mins => %(lease)s))) "
         "AND (%(pids)s::text[] IS NULL OR q.politician_ids && %(pids)s::text[])")
PROP_SQL = ("SELECT q.*, p.canonical_text, p.subject, p.predicate, p.vals, p.place, p.period_start, p.period_end, "
            "p.topic_id FROM ev_proposition_queue q JOIN proposition p USING (proposition_id) ")


def queued(conn, politician_ids: list[str] | None, limit: int | None) -> list[dict]:
    return conn.execute(PROP_SQL + "WHERE " + READY + " ORDER BY q.priority DESC, q.proposition_id LIMIT %(limit)s",
                        {"pids": politician_ids or None, "lease": MAPPING_LEASE_MINUTES, "limit": limit}).fetchall()


def claim_next(conn, politician_ids: list[str] | None, mapper_version: str, exclude: list[str]) -> dict | None:
    """Atomically take the highest-priority ready proposition (`FOR UPDATE SKIP LOCKED`), so workers can share the queue."""
    row = conn.execute(
        "UPDATE ev_proposition_queue SET status = 'mapping', mapper_version = %(mv)s, updated_at = now() "
        "WHERE proposition_id = (SELECT q.proposition_id FROM ev_proposition_queue q WHERE " + READY +
        " AND NOT (q.proposition_id = ANY(%(exclude)s)) ORDER BY q.priority DESC, q.proposition_id "
        "LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING proposition_id",
        {"pids": politician_ids or None, "lease": MAPPING_LEASE_MINUTES, "mv": mapper_version,
         "exclude": exclude}).fetchone()
    if row is None:
        return None
    return conn.execute(PROP_SQL + "WHERE q.proposition_id = %s", (row["proposition_id"],)).fetchone()


def quote_in_text(quote: str | None, text: str) -> bool:
    q = normalize_text(quote or "")
    return len(q) >= 12 and q in normalize_text(text)


class Enricher:
    def __init__(self, conn, settings, *, llm, gateway, fetcher, client, country: str = "MX",
                 sleep=time.sleep, now=None):
        self.conn, self.settings, self.llm, self.gateway, self.fetcher, self.client = (
            conn, settings, llm, gateway, fetcher, client)
        self.country = country.upper()
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ discovery
    def plan(self, prop: dict, claims: list[dict]) -> tuple[list[dict], dict]:
        prompt = json.dumps({
            "claim": prop["canonical_text"], "subject": prop["subject"], "predicate": prop["predicate"],
            "values": prop["vals"], "place": prop["place"], "period_start": str(prop["period_start"] or ""),
            "period_end": str(prop["period_end"] or ""), "claim_types": prop["claim_types"],
            "stated_at": prop["stated_at"].date().isoformat(), "quotes": [c["quote"] for c in claims[:3]],
        }, ensure_ascii=False)
        out, usage = self.llm.extract(PLAN_SYSTEM, prompt, {"input_schema": PLAN_SCHEMA})
        seen, rows = set(), []
        for q in out.get("queries") or []:
            if q["row"] in ROWS and q["row"] not in seen and not q["skip"] and q["query"].strip():
                seen.add(q["row"])
                rows.append(q)
        return rows, usage

    def _domains(self, classes: list[str]) -> list[str]:
        return [r["domain"] for r in self.conn.execute(
            "SELECT domain FROM ev_source_registry WHERE list = 'allow' AND source_class = ANY(%s) ORDER BY domain",
            (classes,))]

    def search(self, prop: dict, rows: list[dict]) -> list[dict]:
        blocked = [r["domain"] for r in self.conn.execute(
            "SELECT domain FROM ev_source_registry WHERE list = 'block'")]
        candidates = []
        for q in rows:
            cfg = ROWS[q["row"]]
            include = self._domains(cfg["classes"]) if cfg.get("classes") else None
            if cfg.get("classes") and not include:
                continue
            params = {"num_results": self.settings.enrich_results_per_query, "include_domains": include,
                      "exclude_domains": None if include else SOCIAL_DOMAINS + blocked,
                      "category": cfg.get("category"), "user_location": cfg.get("location")}
            resp = self.gateway.search_meta(q["query"], purpose="ev:claim", text_chars=None, highlights=False,
                                            store_text=False, **params)
            self._log_request(prop, q, params, resp)
            for rank, r in enumerate(resp["results"]):
                if r.get("url"):
                    candidates.append({"row": q["row"], "rank": rank, **{k: r.get(k) for k in (
                        "url", "title", "published_date", "author", "score")}})
        picked, seen = [], set()
        for c in sorted(candidates, key=lambda c: (c["rank"], list(ROWS).index(c["row"]))):
            key = canonical_url(c["url"])
            if key not in seen and not any(
                    key.split("/")[2] == d or key.split("/")[2].endswith("." + d) for d in SOCIAL_DOMAINS):
                seen.add(key)
                picked.append(c)
        return picked[:self.settings.enrich_max_documents]

    def _log_request(self, prop: dict, q: dict, params: dict, resp: dict) -> None:
        full = {"query": q["query"], "row": q["row"], **params}
        params_hash = hashlib.sha256(json.dumps(full, sort_keys=True).encode()).hexdigest()
        request_id = resp.get("request_id") or f"local-{params_hash[:24]}"
        if resp.get("cached"):
            request_id = f"cached-{params_hash[:16]}-{short_hash(prop['proposition_id'], 8)}"
        self.conn.execute(
            "INSERT INTO ev_exa_request(request_id, worker, purpose, proposition_id, params_hash, params, latency_ms, "
            "result_count, cost_usd, requested_at) VALUES (%s,'enrich','ev:claim',%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (request_id) DO NOTHING",
            (request_id, prop["proposition_id"], params_hash, json.dumps(full), resp.get("latency_ms") or 0,
             len(resp["results"]), resp.get("cost_usd"), self.now()))
        for rank, r in enumerate(resp["results"]):
            if not r.get("url"):
                continue
            self.conn.execute(
                "INSERT INTO ev_discovered_url(url_hash, request_id, url, title, exa_published_date, author, score, "
                "rank, discovered_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (hashlib.sha256(canonical_url(r["url"]).encode()).hexdigest(), request_id, r["url"], r.get("title"),
                 r.get("published_date"), r.get("author"), r.get("score"), rank, self.now()))

    # ------------------------------------------------------------------ judging
    def assess(self, prop: dict, claims: list[dict], cand: dict, fetched: dict) -> dict:
        doc = self.conn.execute(
            "SELECT d.*, r.publisher FROM ev_document d LEFT JOIN ev_source_registry r ON r.domain = d.publisher_domain "
            "WHERE d.document_id = %s", (fetched["document_id"],)).fetchone()
        mapper = self.settings.mapper_version
        existing = self.conn.execute(
            "SELECT * FROM ev_evidence_link WHERE proposition_id = %s AND document_id = %s AND content_sha256 = %s "
            "AND mapper_version = %s", (prop["proposition_id"], doc["document_id"], fetched["content_sha256"],
                                        mapper)).fetchone()
        if existing:
            return dict(existing) | {"reused": True}
        text = fetched["text"][:self.settings.enrich_page_chars]
        prompt = json.dumps({
            "claim": prop["canonical_text"], "stated_at": prop["stated_at"].date().isoformat(),
            "claim_quotes": [c["quote"] for c in claims[:3]],
            "page": {"publisher": doc["publisher"] or doc["publisher_domain"], "title": doc["title"],
                     "published_at": doc["published_at"].date().isoformat() if doc["published_at"] else None,
                     "url": doc["resolved_url"], "text": text},
        }, ensure_ascii=False)
        out, usage = self.llm.extract(ASSESS_SYSTEM, prompt, {"input_schema": ASSESS_SCHEMA})
        quote = (out.get("quote") or "").strip() or None
        verified = quote_in_text(quote, fetched["text"])
        link = {
            "link_id": "link:" + short_hash(f"{prop['proposition_id']}|{doc['document_id']}|"
                                            f"{fetched['content_sha256']}|{mapper}", 24),
            "proposition_id": prop["proposition_id"], "document_id": doc["document_id"],
            "content_sha256": fetched["content_sha256"], "query_row": cand["row"], "stance": out["stance"],
            "confidence": max(0.0, min(1.0, float(out["confidence"]))), "summary": out["summary"].strip(),
            "quote": quote, "quote_verified": verified, "rationale": out["rationale"].strip(),
            "mapper_version": mapper, "model": self.llm.model, "usage": json.dumps(usage), "assessed_at": self.now(),
        }
        self.conn.execute(
            f"INSERT INTO ev_evidence_link({', '.join(link)}) VALUES ({', '.join(['%s'] * len(link))}) "
            "ON CONFLICT (proposition_id, document_id, content_sha256, mapper_version) DO NOTHING",
            list(link.values()))
        return link

    # ------------------------------------------------------------------ orchestration
    def enrich_one(self, prop: dict) -> dict:
        claims = self.conn.execute(CLAIMS_SQL, (prop["proposition_id"],)).fetchall()
        if not claims:
            self._set_status(prop, "skipped", "no_current_claims")
            return {"proposition_id": prop["proposition_id"], "status": "skipped"}
        try:
            rows, _ = self.plan(prop, claims)
            candidates = self.search(prop, rows)
            stances: dict[str, int] = {}
            fetched_n = 0
            for cand in candidates:
                fetched = self.fetcher.fetch(cand["url"], cand.get("published_date"), include_text=True)
                if fetched.get("fetch_status") != "fetched" or not (fetched.get("text") or "").strip():
                    continue
                fetched_n += 1
                link = self.assess(prop, claims, cand, fetched)
                key = link["stance"] if link["quote_verified"] or link["stance"] == "irrelevant" else "unverified"
                stances[key] = stances.get(key, 0) + 1
            pushed = projection.project(self.conn, self.client, prop, claims, country=self.country,
                                        mapper_version=self.settings.mapper_version, sleep=self.sleep, now=self.now)
        except Exception:
            self._set_status(prop, "queued")
            raise
        # A rejected batch, a failed ingestion or one still processing leaves the proposition queued so a later run
        # re-projects it (unchanged items are not resent).
        status = "queued" if pushed["errors"] or pushed["pending"] else "mapped"
        self._set_status(prop, status)
        return {"proposition_id": prop["proposition_id"], "status": status, "queries": len(rows),
                "candidates": len(candidates), "fetched": fetched_n, "stances": stances, "pastdev": pushed}

    def _set_status(self, prop: dict, status: str, reason: str | None = None) -> None:
        self.conn.execute(
            "UPDATE ev_proposition_queue SET status = %s, skip_reason = %s, mapper_version = %s, "
            "mapped_at = CASE WHEN %s = 'mapped' THEN %s ELSE mapped_at END, updated_at = %s "
            "WHERE proposition_id = %s",
            (status, reason, self.settings.mapper_version, status, self.now(), self.now(), prop["proposition_id"]))

    def run(self, politician_ids: list[str] | None, limit: int | None) -> dict:
        results, errors, seen = [], [], []
        while limit is None or len(seen) < limit:
            prop = claim_next(self.conn, politician_ids, self.settings.mapper_version, seen)
            if prop is None:
                break
            seen.append(prop["proposition_id"])
            try:
                results.append(self.enrich_one(prop))
            except Exception as exc:  # one proposition's failure must not stop the batch
                log.error("enrich.failed", proposition_id=prop["proposition_id"], error=str(exc)[:300])
                errors.append({"proposition_id": prop["proposition_id"], "error": str(exc)[:300]})
        return {"propositions": len(results), "results": results, "errors": errors,
                "exa_usd": round(self.gateway.usage.spent, 4)}


def dry_run(conn, settings, politician_ids: list[str] | None, limit: int | None) -> dict:
    props = queued(conn, politician_ids, limit)
    return {"dry_run": True, "propositions": len(props),
            **estimate(len(props), settings.enrich_max_documents, settings.enrich_page_chars),
            "top": [{"proposition_id": p["proposition_id"], "claim": p["canonical_text"],
                     "politicians": p["politician_ids"], "priority": round(p["priority"], 2)} for p in props[:20]]}
