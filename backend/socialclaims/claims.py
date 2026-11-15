"""Phase 3: claim distillation from Phase 2 segments, grounding, and strict proposition clustering.

One structured LLM call per post. Model output is never trusted as is: every quote must align with the cited
segments, paraphrases may not add numbers or flip negation, topics must be in the country's taxonomy, and the
politician is only the speaker when Phase 2 attributed speech to them. Failures are kept and flagged, not deleted.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import httpx
from psycopg.types.json import Jsonb

from polgraph.ids import normalize_text

from .capture import utcnow
from .identity import merged_ids
from .segments import PROCESSOR_VERSION as SEGMENTS_VERSION
from .systema import GraphReader, read_person

TAXONOMY_DIR = Path(__file__).parent / "taxonomy"
MAX_SEGMENT_CHARS = 12_000
FUZZY_MIN = 0.9
NEGATIONS = {"no", "nunca", "jamas", "ni", "tampoco", "ningun", "ninguna", "ninguno", "nada", "nadie", "sin"}
CLAIM_TYPES = ["factual", "statistic", "policy_position", "commitment", "forecast", "accusation", "opinion"]

SYSTEM_PROMPT = """You extract claims from one Mexican social-media post for an internal, evidence-only research archive.
A claim is a specific proposition explicitly expressed in the speech, on-screen text or caption segments given.
Greetings, calls to action, jokes with no proposition, slogans and song lyrics are not claims. Return zero claims if
there are none. Rules:
- quote: copy the exact original-language words from the cited segments, verbatim, no edits or ellipses.
- segment_ids: the [sN] ids the quote comes from (consecutive speech segments may be combined).
- text: a standalone Spanish paraphrase. Resolve pronouns and relative dates using the publication date, but never
  strengthen the claim, add facts, or change any number, date, scope or negation. text_en: English translation of text.
- relation_to_politician: speaker only if the post metadata says the tracked politician is the probable speaker
  and the quote is in their speech; otherwise subject (claim is about them), quoted (someone reports their words),
  or mentioned.
- modality: asserted, questioned, quoted (the uploader reports someone else's words) or satire.
- topics: ids from the given taxonomy only.
- referenced_period: the date range the claim is about (ISO dates), or null if none is stated or implied.
- proposition: the claim's canonical structure; values are the exact numbers with units stated in the quote.
- checkworthiness: 0-1, high when specific, verifiable and of public interest."""


def _claim_schema(topic_ids: list[str]) -> dict:
    s, arr = {"type": "string"}, lambda t: {"type": "array", "items": t}
    value = {"type": "object", "properties": {"value": s, "unit": s}, "required": ["value"]}
    claim = {"type": "object", "properties": {
        "segment_ids": arr(s), "quote": s, "text": s, "text_en": s,
        "type": {"type": "string", "enum": CLAIM_TYPES},
        "modality": {"type": "string", "enum": ["asserted", "questioned", "quoted", "satire"]},
        "relation_to_politician": {"type": "string", "enum": ["speaker", "subject", "quoted", "mentioned"]},
        "topics": arr({"type": "string", "enum": topic_ids}),
        "entities": {"type": "object", "properties": {"people": arr(s), "organizations": arr(s), "places": arr(s),
                                                      "numbers": arr(value)}},
        "checkworthiness": {"type": "number"},
        "referenced_period": {"type": ["object", "null"], "properties": {"start": s, "end": s}},
        "proposition": {"type": "object", "properties": {
            "subject": s, "predicate": s, "negated": {"type": "boolean"}, "values": arr(value),
            "place": {"type": ["string", "null"]}, "qualifiers": arr(s)},
            "required": ["subject", "predicate", "negated"]}},
        "required": ["segment_ids", "quote", "text", "type", "modality", "relation_to_politician", "topics",
                     "checkworthiness", "proposition"]}
    return {"name": "record_claims", "description": "Record every claim in the post (possibly none).",
            "input_schema": {"type": "object", "properties": {"claims": arr(claim)}, "required": ["claims"]}}


def load_taxonomy(country_code: str) -> dict:
    return json.loads((TAXONOMY_DIR / f"{country_code.upper()}.json").read_text())


class LLM(Protocol):
    model: str

    def extract(self, system: str, prompt: str, tool: dict) -> tuple[dict, dict]:
        """Returns (tool input, usage)."""


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep, max_tokens: int = 8000):
        self.model, self.max_tokens, self.sleep = model, max_tokens, sleep
        self.http = httpx.Client(base_url=base_url, timeout=180, transport=transport,
                                 headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"})

    def extract(self, system: str, prompt: str, tool: dict) -> tuple[dict, dict]:
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}], "tools": [tool],
                "tool_choice": {"type": "tool", "name": tool["name"]}}
        for attempt in range(4):
            try:
                r = self.http.post("/v1/messages", json=body)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                self.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 529) and attempt < 3:
                self.sleep(2 ** attempt * 5)
                continue
            r.raise_for_status()
            data = r.json()
            block = next(b for b in data["content"] if b["type"] == "tool_use")
            return block["input"], data.get("usage", {})
        raise RuntimeError("unreachable")


def extractor_version(model: str, taxonomy: dict) -> str:
    tool = _claim_schema([t["id"] for t in taxonomy["topics"]])
    h = hashlib.sha256((SYSTEM_PROMPT + json.dumps(tool, sort_keys=True) + taxonomy["version"]).encode())
    return f"{model}:{h.hexdigest()[:12]}"


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Casefolded text with whitespace runs collapsed, plus each output char's index in `text`."""
    out, idx, space = [], [], False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not space and out:
                out.append(" ")
                idx.append(i)
            space = True
            continue
        space = False
        for c in ch.casefold():
            out.append(c)
            idx.append(i)
    return "".join(out).rstrip(), idx


def align(quote: str, source: str) -> tuple[str, int | None, int | None, float]:
    """('exact'|'fuzzy'|'none', start, end, ratio) of `quote` within `source`, offsets into `source`."""
    ns, idx = _norm_map(source)
    nq, _ = _norm_map(quote)
    if not nq:
        return "none", None, None, 0.0
    at = ns.find(nq)
    if at >= 0:
        return "exact", idx[at], idx[at + len(nq) - 1] + 1, 1.0
    m = SequenceMatcher(None, ns, nq, autojunk=False).find_longest_match(0, len(ns), 0, len(nq))
    best = (0.0, 0)
    lo = max(0, m.a - m.b - 10)
    for start in range(lo, min(len(ns), m.a - m.b + 10) + 1):
        ratio = SequenceMatcher(None, ns[start:start + len(nq)], nq, autojunk=False).ratio()
        if ratio > best[0]:
            best = (ratio, start)
    ratio, start = best
    if ratio >= FUZZY_MIN:
        end = min(len(ns), start + len(nq)) - 1
        return "fuzzy", idx[start], idx[end] + 1, ratio
    return "none", None, None, ratio


def _number(raw: str) -> str:
    """'1,250,000' -> '1250000' (thousands), '2,5' -> '2.5' (Spanish decimal comma), '2.5' unchanged."""
    raw = raw.rstrip(".,")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):
        return raw.replace(",", "")
    return raw.replace(",", ".")


def _numbers(text: str) -> set[str]:
    return {_number(n) for n in re.findall(r"\d[\d,.]*", text or "")}


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _prop_id(p: dict, period: tuple[date | None, date | None]) -> str:
    key = [normalize_text(p.get("subject") or ""), normalize_text(p.get("predicate") or ""), bool(p.get("negated")),
           sorted(f"{_numbers(str(v.get('value')))}|{normalize_text(v.get('unit') or '')}" for v in p.get("values") or []),
           normalize_text(p.get("place") or ""), [str(d) if d else None for d in period],
           sorted(normalize_text(q) for q in p.get("qualifiers") or [])]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]


def build_prompt(snap, post_meta: dict, segs: list[dict], taxonomy: dict) -> tuple[str, dict[str, dict]]:
    """Prompt text and the [sN] -> segment map it cites. Comments are never included."""
    refs: dict[str, dict] = {}
    lines, used, seen = [], 0, set()
    for s in sorted(segs, key=lambda s: ({"speech": 0, "caption": 1, "onscreen": 2}[s["source"]], s["ordinal"])):
        key = (s["source"], normalize_text(s["text"]))
        if key in seen:
            continue
        seen.add(key)
        if used + len(s["text"]) > MAX_SEGMENT_CHARS:
            break
        used += len(s["text"])
        ref = f"s{s['ordinal']}"
        refs[ref] = s
        t = f" {s['start_s']:.1f}-{s['end_s']:.1f}s" if s["start_s"] is not None and s["end_s"] is not None else ""
        lines.append(f"[{ref}] ({s['source']}{t}) {s['text']}")
    profile = {"politician": snap.name, "aliases": snap.aliases[:10],
               "offices": [o.name for o in snap.current_offices], "parties": [p.get("acronym") or p.get("name") for p in snap.parties]}
    topics = "\n".join(f"- {t['id']}: {t['es']}" for t in taxonomy["topics"])
    prompt = (f"Tracked politician:\n{json.dumps(profile, ensure_ascii=False, default=str)}\n\n"
              f"Post metadata:\n{json.dumps(post_meta, ensure_ascii=False, default=str)}\n\n"
              f"Topic taxonomy:\n{topics}\n\nSegments:\n" + "\n".join(lines))
    return prompt, refs


@dataclass
class Extractor:
    conn: object
    graph: GraphReader
    llm: LLM
    country_code: str = "MX"
    clock: object = utcnow

    def __post_init__(self):
        self.taxonomy = load_taxonomy(self.country_code)
        self.topic_ids = {t["id"] for t in self.taxonomy["topics"]}
        self.version = extractor_version(self.llm.model, self.taxonomy)
        self.tool = _claim_schema(sorted(self.topic_ids))

    def pending(self, politician_id: str) -> list[dict]:
        return self.conn.execute(
            "SELECT DISTINCT pp.oriane_id, pp.raw_id, pp.canonical_oriane_id, p.published_at, p.platform, "
            "pp.uploader_handle, pp.low_information FROM run_post rp "
            "JOIN post p ON p.oriane_id = rp.oriane_id "
            "JOIN post_processing pp ON pp.oriane_id = p.oriane_id AND pp.raw_id = p.latest_raw_id "
            "AND pp.processor_version = %s "
            "LEFT JOIN claim_extraction ce ON ce.oriane_id = pp.oriane_id AND ce.raw_id = pp.raw_id "
            "AND ce.extractor_version = %s AND ce.politician_id = %s AND ce.status = 'ok' "
            "WHERE rp.politician_id = ANY(%s) AND rp.relevance <> 'low' AND ce.oriane_id IS NULL "
            "AND pp.canonical_oriane_id = pp.oriane_id ORDER BY p.published_at DESC, pp.oriane_id",
            (SEGMENTS_VERSION, self.version, politician_id, merged_ids(self.conn, politician_id))).fetchall()

    def run(self, politician_id: str, max_posts: int) -> dict:
        snap = read_person(self.graph, politician_id)
        if snap is None:
            raise LookupError(f"{politician_id} not in System A")
        todo = self.pending(politician_id)
        out = {"politician_id": politician_id, "extractor_version": self.version, "pending": len(todo),
               "extracted": 0, "failed": 0, "claims": 0, "grounding": {}, "input_tokens": 0, "output_tokens": 0}
        for row in todo[:max_posts]:
            res = self.extract_post(snap, row)
            out["extracted" if res["status"] == "ok" else "failed"] += 1
            out["claims"] += res["claims"]
            out["input_tokens"] += res.get("input_tokens") or 0
            out["output_tokens"] += res.get("output_tokens") or 0
            for g, n in res.get("grounding", {}).items():
                out["grounding"][g] = out["grounding"].get(g, 0) + n
        return out

    def extract_post(self, snap, row: dict) -> dict:
        now = self.clock()
        pid = snap.politician_id
        segs = self.conn.execute(
            "SELECT segment_id, source, ordinal, text, start_s, end_s FROM segment WHERE oriane_id = %s AND raw_id = %s "
            "AND processor_version = %s AND source <> 'comment' ORDER BY ordinal",
            (row["oriane_id"], row["raw_id"], SEGMENTS_VERSION)).fetchall()
        roles = {r["role"]: r for r in self.conn.execute(
            "SELECT role, status, evidence FROM post_person_role WHERE oriane_id = %s AND raw_id = %s "
            "AND processor_version = %s AND person_id = %s", (row["oriane_id"], row["raw_id"], SEGMENTS_VERSION, pid))}
        speaker = roles.get("speaker") or {"status": "unknown", "evidence": []}
        meta = {"platform": row["platform"], "uploader": row["uploader_handle"],
                "published_at": row["published_at"].isoformat(),
                "politician_roles": {k: v["status"] for k, v in roles.items()},
                "politician_is_probable_speaker": speaker["status"] in ("verified", "probable")}
        record = {"status": "ok", "claims": 0, "grounding": {}}
        if not segs:
            claims, usage = [], {}
        else:
            prompt, refs = build_prompt(snap, meta, segs, self.taxonomy)
            try:
                result, usage = self.llm.extract(SYSTEM_PROMPT, prompt, self.tool)
                claims = result.get("claims") or []
            except Exception as exc:  # noqa: BLE001 - recorded, run continues with the next post
                self._log(row, pid, "failed", 0, {}, f"{type(exc).__name__}: {exc}"[:500], now)
                return {"status": "failed", "claims": 0}
        with self.conn.transaction():
            for i, c in enumerate(claims):
                g = self._store_claim(row, pid, i, c, refs, speaker, now)
                record["grounding"][g] = record["grounding"].get(g, 0) + 1
            self._log(row, pid, "ok", len(claims), usage, None, now)
        record.update(claims=len(claims), input_tokens=usage.get("input_tokens"),
                      output_tokens=usage.get("output_tokens"))
        return record

    def _log(self, row, pid, status, n, usage, error, now):
        self.conn.execute(
            "INSERT INTO claim_extraction(oriane_id, raw_id, extractor_version, politician_id, model, status, claims, "
            "input_tokens, output_tokens, error, extracted_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (oriane_id, raw_id, extractor_version, politician_id) DO UPDATE SET status = EXCLUDED.status, "
            "claims = EXCLUDED.claims, input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens, "
            "error = EXCLUDED.error, extracted_at = EXCLUDED.extracted_at",
            (row["oriane_id"], row["raw_id"], self.version, pid, self.llm.model, status, n,
             usage.get("input_tokens"), usage.get("output_tokens"), error, now))

    def _store_claim(self, row, pid, i, c: dict, refs: dict, speaker: dict, now) -> str:
        issues: list[str] = []
        cited = sorted((refs[r] for r in dict.fromkeys(c.get("segment_ids") or []) if r in refs),
                       key=lambda s: s["ordinal"])
        if len(cited) < len(c.get("segment_ids") or []):
            issues.append("unknown_segment_id")
        quote = (c.get("quote") or "").strip()
        kind, start, end, ratio = align(quote, " ".join(s["text"] for s in cited)) if cited else ("none", None, None, 0)
        if kind == "none":
            # The model may cite the wrong segment; search every segment it was shown before giving up.
            for s in refs.values():
                k2, st2, en2, r2 = align(quote, s["text"])
                if k2 != "none":
                    kind, start, end, ratio, cited = k2, st2, en2, r2, [s]
                    issues.append("segment_id_corrected")
                    break
        grounding = {"exact": "exact", "fuzzy": "review", "none": "failed"}[kind]
        if kind == "fuzzy":
            issues.append(f"quote_not_exact:{ratio:.2f}")
        if kind == "none":
            issues.append("quote_not_in_segments")
        period = c.get("referenced_period") or {}
        p_start, p_end = _date(period.get("start")), _date(period.get("end"))
        if period and (period.get("start") or period.get("end")) and not (p_start or p_end):
            issues.append("bad_referenced_period")
        stated = row["published_at"]
        allowed = _numbers(quote) | {str(d.year) for d in (p_start, p_end) if d} | {str(stated.year)} | \
            {str(x) for d in (p_start, p_end) if d for x in (d.month, d.day)}
        if extra := sorted(_numbers(c.get("text")) - allowed):
            issues.append(f"paraphrase_numbers_not_in_quote:{','.join(extra)}")
        prop = c.get("proposition") or {}
        vals = {n for v in prop.get("values") or [] for n in _numbers(str(v.get("value")))}
        if vals - _numbers(quote):
            issues.append("proposition_values_not_in_quote")
        neg = lambda t: bool(NEGATIONS & set(normalize_text(t or "").split()))  # noqa: E731
        if neg(quote) != neg(c.get("text")):
            issues.append("negation_mismatch")
        if bool(prop.get("negated")) != neg(quote):
            issues.append("proposition_negation_mismatch")
        topics = [t for t in c.get("topics") or [] if t in self.topic_ids]
        if len(topics) < len(c.get("topics") or []):
            issues.append("unknown_topic")
        relation = c.get("relation_to_politician")
        if relation not in ("speaker", "subject", "quoted", "mentioned"):
            relation, _ = "mentioned", issues.append("bad_relation")
        has_speaker = speaker["status"] in ("verified", "probable")
        if relation == "speaker" and not (has_speaker and any(s["source"] == "speech" for s in cited)):
            relation = "mentioned"
            issues.append("speaker_relation_without_evidence")
        ctype = c.get("type") if c.get("type") in CLAIM_TYPES else "factual"
        modality = c.get("modality") if c.get("modality") in ("asserted", "questioned", "quoted", "satire") else "asserted"
        if issues and grounding == "exact":
            grounding = "review"
        timed = [s for s in cited if s["start_s"] is not None]
        prop_id = None
        if grounding != "failed" and prop.get("subject") and prop.get("predicate"):
            prop_id = _prop_id(prop, (p_start, p_end))
            self.conn.execute(
                "INSERT INTO proposition(proposition_id, canonical_text, subject, predicate, negated, vals, place, "
                "period_start, period_end, qualifiers, topic_id, created_at, first_stated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (proposition_id) DO UPDATE SET "
                "first_stated_at = LEAST(proposition.first_stated_at, EXCLUDED.first_stated_at)",
                (prop_id, c.get("text") or quote, prop["subject"], prop["predicate"], bool(prop.get("negated")),
                 Jsonb(prop.get("values") or []), prop.get("place"), p_start, p_end, Jsonb(prop.get("qualifiers") or []),
                 topics[0] if topics else None, now, stated))
        claim_id = hashlib.sha256(f"{self.version}|{row['oriane_id']}|{row['raw_id']}|{pid}|{i}|{quote}".encode()
                                  ).hexdigest()[:24]
        self.conn.execute(
            "INSERT INTO distilled_claim(claim_id, oriane_id, raw_id, politician_id, segment_ids, proposition_id, quote, "
            "quote_char_start, quote_char_end, text, text_en, speaker_id, speaker_status, speaker_evidence, "
            "relation_to_politician, type, topics, entities, checkworthiness, modality, grounding, grounding_issues, "
            "stated_at, quote_start_s, quote_end_s, referenced_period_start, referenced_period_end, extractor_version, "
            "extracted_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (claim_id) DO NOTHING",
            (claim_id, row["oriane_id"], row["raw_id"], pid, [s["segment_id"] for s in cited], prop_id, quote,
             start, end, c.get("text") or "", c.get("text_en"), pid if relation == "speaker" else None,
             speaker["status"] if relation == "speaker" else "unknown",
             Jsonb(speaker["evidence"] if relation == "speaker" else []), relation, ctype, topics, Jsonb(c.get("entities") or {}),
             max(0.0, min(1.0, float(c.get("checkworthiness") or 0))), modality, grounding, Jsonb(issues), stated,
             min((s["start_s"] for s in timed), default=None),
             max((s["end_s"] for s in timed if s["end_s"] is not None), default=None),
             p_start, p_end, self.version, now))
        return grounding
