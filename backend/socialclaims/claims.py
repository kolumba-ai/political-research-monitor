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
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterator, Protocol

import httpx
from psycopg.types.json import Jsonb

from polgraph.exa_gateway import PgRateLimiter, retry_after_seconds
from polgraph.ids import normalize_text, strip_accents

from .capture import utcnow
from .identity import merged_ids
from .segments import PROCESSOR_VERSION as SEGMENTS_VERSION
from .systema import GraphReader, read_person

TAXONOMY_DIR = Path(__file__).parent / "taxonomy"
MAX_SEGMENT_CHARS = 12_000
FUZZY_MIN = 0.9
CHECKS_VERSION = "checks-5"
NEGATIONS = {"no", "nunca", "jamas", "ni", "tampoco", "ningun", "ninguna", "ninguno", "nada", "nadie", "sin"}
CLAIM_TYPES = ["factual", "statistic", "policy_position", "commitment", "forecast", "accusation", "opinion"]
INFO_ISSUES = {"predicate_negation_normalized", "segment_id_corrected"}
NUMBER_WORDS = {
    "cero": 0, "ninguno": 0, "ninguna": 0, "ningun": 0,
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "veinte": 20,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "cien": 100, "ciento": 100,
    "mil": 1000,
}
NEGATION_FILLERS = {"lo", "la", "los", "las", "le", "les", "se", "me", "te", "nos", "ya", "nunca", "mas"}

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
- topics: 1-3 ids from the given taxonomy only, most specific first. Use gestion_gobierno only when no specific
  topic fits.
- referenced_period: the date range the claim is about (ISO dates), or null if none is stated or implied.
- proposition: the claim's canonical structure. predicate is worded affirmatively and any negation goes only in
  negated ("no sale a defender" -> predicate "sale a defender", negated true). values are the numbers stated in the
  quote, written as digits, with the scale word in unit ("6 mil 500 millones de pesos" -> value "6500", unit
  "millones de pesos"; "dos puentes" -> value "2", unit "puentes").
- checkworthiness: 0-1, high when specific, verifiable and of public interest."""


def _claim_schema(topic_ids: list[str]) -> dict:
    s, arr = {"type": "string"}, lambda t: {"type": "array", "items": t}
    value = {"type": "object", "properties": {"value": s, "unit": s}, "required": ["value"], "additionalProperties": False}
    claim = {"type": "object", "properties": {
        "segment_ids": arr(s), "quote": s, "text": s, "text_en": s,
        "type": {"type": "string", "enum": CLAIM_TYPES},
        "modality": {"type": "string", "enum": ["asserted", "questioned", "quoted", "satire"]},
        "relation_to_politician": {"type": "string", "enum": ["speaker", "subject", "quoted", "mentioned"]},
        "topics": arr({"type": "string", "enum": topic_ids}),
        "entities": {"type": "object", "properties": {"people": arr(s), "organizations": arr(s), "places": arr(s),
                                                      "numbers": arr(value)}, "additionalProperties": False},
        "checkworthiness": {"type": "number"},
        "referenced_period": {"type": ["object", "null"], "properties": {"start": s, "end": s},
                              "additionalProperties": False},
        "proposition": {"type": "object", "properties": {
            "subject": s, "predicate": s, "negated": {"type": "boolean"}, "values": arr(value),
            "place": {"type": ["string", "null"]}, "qualifiers": arr(s)},
            "required": ["subject", "predicate", "negated"], "additionalProperties": False}},
        "required": ["segment_ids", "quote", "text", "type", "modality", "relation_to_politician", "topics",
                     "checkworthiness", "proposition"], "additionalProperties": False}
    return {"name": "record_claims", "description": "Record every claim in the post (possibly none).",
            "input_schema": {"type": "object", "properties": {"claims": arr(claim)}, "required": ["claims"],
                             "additionalProperties": False}}


def load_taxonomy(country_code: str) -> dict:
    return json.loads((TAXONOMY_DIR / f"{country_code.upper()}.json").read_text())


class LLM(Protocol):
    model: str

    def extract(self, system: str, prompt: str, tool: dict) -> tuple[dict, dict]:
        """Returns (schema-conforming output, usage)."""


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep, max_tokens: int = 8000,
                 rate_limiter_database_url: str | None = None, requests_per_second: float = 2.0, limiter=None):
        self.model, self.max_tokens, self.sleep = model, max_tokens, sleep
        self.limiter = limiter if limiter is not None else (
            PgRateLimiter(rate_limiter_database_url, f"anthropic:{api_key}", requests_per_second)
            if rate_limiter_database_url else None
        )
        self.http = httpx.Client(base_url=base_url, timeout=180, transport=transport,
                                 headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"})

    def extract(self, system: str, prompt: str, tool: dict) -> tuple[dict, dict]:
        # Current models reject forced tool_choice and temperature; structured outputs enforce the schema instead.
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {"format": {"type": "json_schema", "schema": tool["input_schema"]}}}
        for attempt in range(4):
            try:
                if self.limiter is not None:
                    self.limiter.wait()
                r = self.http.post("/v1/messages", json=body)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                self.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 529) and attempt < 3:
                delay = retry_after_seconds(r.headers) if r.status_code in (429, 503) else None
                self.sleep(2 ** attempt * 5 if delay is None else delay)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"anthropic {r.status_code}: {r.text[:500]}")
            data = r.json()
            if data.get("stop_reason") == "max_tokens":
                raise RuntimeError("anthropic output truncated at max_tokens")
            text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
            return json.loads(text), data.get("usage", {})
        raise RuntimeError("unreachable")


class OpenAILLM(AnthropicLLM):
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com",
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep, max_tokens: int = 8000,
                 rate_limiter_database_url: str | None = None, requests_per_second: float = 5.0, limiter=None):
        self.model, self.max_tokens, self.sleep = model, max_tokens, sleep
        self.limiter = limiter if limiter is not None else (
            PgRateLimiter(rate_limiter_database_url, f"openai:{api_key}", requests_per_second)
            if rate_limiter_database_url else None
        )
        self.http = httpx.Client(base_url=base_url, timeout=300, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def extract(self, system: str, prompt: str, tool: dict) -> tuple[dict, dict]:
        body = {"model": self.model, "max_completion_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": tool["name"], "schema": tool["input_schema"], "strict": False}}}
        for attempt in range(4):
            try:
                if self.limiter is not None:
                    self.limiter.wait()
                r = self.http.post("/v1/chat/completions", json=body)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                self.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                delay = retry_after_seconds(r.headers) if r.status_code in (429, 503) else None
                self.sleep(2 ** attempt * 5 if delay is None else delay)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"openai {r.status_code}: {r.text[:500]}")
            data = r.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("openai output truncated at max_completion_tokens")
            u = data.get("usage", {})
            output = json.loads(choice["message"]["content"])
            if not isinstance(output, dict) or not isinstance(output.get("claims"), list):
                raise RuntimeError("openai response missing claims list")
            return output, {
                "input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}
        raise RuntimeError("unreachable")


def extractor_version(model: str, taxonomy: dict, checks_version: str = CHECKS_VERSION) -> str:
    tool = _claim_schema([t["id"] for t in taxonomy["topics"]])
    h = hashlib.sha256((checks_version + SYSTEM_PROMPT + json.dumps(tool, sort_keys=True) +
                        taxonomy["version"]).encode())
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


def _number(raw: str, decimal_percent: bool = False) -> str:
    """Canonicalize common Spanish and English numeric formats."""
    raw = raw.rstrip(".,")
    if not decimal_percent and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        return raw.replace(".", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):
        number = raw.replace(",", "")
    else:
        number = raw.replace(",", ".")
    return re.sub(r"\.0+$", "", number)


def _fmt_decimal(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _number_values(text: str, decimal_percent: bool = False) -> Iterator[tuple[str, bool]]:
    raw = text or ""
    tokens = list(re.finditer(r"\d[\d,.]*|[^\W\d_]+", raw, flags=re.UNICODE))

    def word(match: re.Match) -> str:
        return strip_accents(match.group()).lower()

    def integer(match: re.Match) -> int | None:
        token = word(match)
        if token in NUMBER_WORDS:
            return Decimal(NUMBER_WORDS[token])
        is_percent = decimal_percent or bool(re.match(r"\s*(?:%|por\s+ciento\b)", raw[match.end():],
                                                      flags=re.IGNORECASE))
        canonical = _number(token, decimal_percent=is_percent)
        return Decimal(canonical) if re.fullmatch(r"\d+(\.\d+)?", canonical) else None

    i = 0
    while i < len(tokens):
        token = word(tokens[i])
        if i + 1 < len(tokens) and word(tokens[i + 1]) == "mil" \
                and raw[tokens[i].end():tokens[i + 1].start()].isspace():
            leading = integer(tokens[i])
            if leading is not None:
                has_trailing = i + 2 < len(tokens) \
                    and raw[tokens[i + 1].end():tokens[i + 2].start()].isspace()
                trailing = integer(tokens[i + 2]) if has_trailing and leading == leading.to_integral() else None
                if trailing is not None and trailing != trailing.to_integral():
                    trailing = None
                total = _fmt_decimal(leading * 1000 + (trailing or 0))
                consumed = tokens[i:i + (3 if trailing is not None else 2)]
                has_digit = any(re.match(r"\d", word(token)) for token in consumed)
                yield str(total), has_digit
                i += 3 if trailing is not None else 2
                continue
        if token in NUMBER_WORDS:
            if token == "ciento" and i > 0 and word(tokens[i - 1]) == "por":
                i += 1
                continue
            yield str(NUMBER_WORDS[token]), False
        elif re.match(r"\d", token):
            is_percent = decimal_percent or bool(re.match(r"\s*(?:%|por\s+ciento\b)", raw[tokens[i].end():],
                                                          flags=re.IGNORECASE))
            yield _number(token, decimal_percent=is_percent), True
        i += 1


def _numbers(text: str, include_words: bool = True, decimal_percent: bool = False) -> set[str]:
    return {value for value, has_digit in _number_values(text, decimal_percent)
            if include_words or has_digit}


def _value_numbers(value: dict) -> list[str]:
    unit = str(value.get("unit") or "").strip()
    decimal_percent = unit == "%" or normalize_text(unit) == "por ciento"
    numbers = _numbers(str(value.get("value")), decimal_percent=decimal_percent)
    if normalize_text(unit).split()[:1] == ["mil"]:  # value 2, unit "mil pesos" -> 2000
        numbers = {_fmt_decimal(Decimal(n) * 1000) if re.fullmatch(r"\d+(\.\d+)?", n) else n for n in numbers}
    return sorted(numbers)


def _negated(text: str) -> bool:
    return bool(NEGATIONS & set(_quote_tokens(text)))


def _quote_tokens(text: str) -> list[str]:
    raw = str(text or "")
    discourse_no = {
        m.start("no") for m in re.finditer(r"(?P<no>\bno)\s*(?:[,?¿]|$)", raw, flags=re.IGNORECASE)
    }
    return [
        normalize_text(m.group()) for m in re.finditer(r"[^\W\d_]+", raw, flags=re.UNICODE)
        if m.start() not in discourse_no
    ]


def _negation_targets_predicate(quote: str, predicate: str) -> bool:
    predicate_words = normalize_text(predicate).split()
    if not predicate_words:
        return False
    prefix = predicate_words[0][:4]
    tokens = _quote_tokens(quote)
    for i, token in enumerate(tokens):
        if token not in NEGATIONS:
            continue
        for distance in (1, 2):
            target = i + distance
            if target < len(tokens) and all(t in NEGATION_FILLERS for t in tokens[i + 1:target]) \
                    and tokens[target].startswith(prefix):
                return True
    return False


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _prop_id(p: dict, period: tuple[date | None, date | None]) -> str:
    values = sorted((_value_numbers(v), normalize_text(v.get("unit") or "")) for v in p.get("values") or [])
    key = [normalize_text(p.get("subject") or ""), normalize_text(p.get("predicate") or ""), bool(p.get("negated")),
           values,
           normalize_text(p.get("place") or ""), [str(d) if d else None for d in period],
           sorted(normalize_text(q) for q in p.get("qualifiers") or [])]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]


def build_prompt(snap, post_meta: dict, segs: list[dict], taxonomy: dict) -> tuple[str, dict[str, dict]]:
    """Prompt text and the [sN] -> segment map it cites. Comments are never included."""
    refs: dict[str, dict] = {}
    lines, used, seen, items = [], 0, set(), []
    for s in sorted(segs, key=lambda s: ({"speech": 0, "caption": 1, "onscreen": 2}[s["source"]], s["ordinal"])):
        key = (s["source"], normalize_text(s["text"]))
        if key not in seen:
            seen.add(key)
            items.append(s)
    # Keep up to 500 chars of headroom for every later segment, so one long transcript cannot hide the caption.
    reserve = [0] * len(items)
    for i in range(len(items) - 2, -1, -1):
        reserve[i] = reserve[i + 1] + min(len(items[i + 1]["text"]), 500)
    for i, s in enumerate(items):
        room = MAX_SEGMENT_CHARS - used - min(reserve[i], MAX_SEGMENT_CHARS // 2)
        if room < min(len(s["text"]), 200):
            continue
        shown = s["text"]
        if len(shown) > room:
            shown = (shown[:room - 6].rsplit(None, 1) or [""])[0] + " [...]"  # cut at a word boundary so no figure is split
        used += len(shown)
        ref = f"s{s['ordinal']}"
        refs[ref] = s
        t = f" {s['start_s']:.1f}-{s['end_s']:.1f}s" if s["start_s"] is not None and s["end_s"] is not None else ""
        lines.append(f"[{ref}] ({s['source']}{t}) {shown}")
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
    skip_any_version: bool = False

    def __post_init__(self):
        self.taxonomy = load_taxonomy(self.country_code)
        self.topic_ids = {t["id"] for t in self.taxonomy["topics"]}
        self.version = extractor_version(self.llm.model, self.taxonomy, CHECKS_VERSION)
        self.tool = _claim_schema(sorted(self.topic_ids))

    def pending(self, politician_id: str, shard: tuple[int, int] = (0, 1)) -> list[dict]:
        index, count = shard
        if count <= 0 or index < 0 or index >= count:
            raise ValueError("shard must satisfy 0 <= index < count")
        rows = self.conn.execute(
            "SELECT DISTINCT pp.oriane_id, pp.raw_id, pp.canonical_oriane_id, p.published_at, p.platform, "
            "pp.uploader_handle, pp.low_information FROM run_post rp "
            "JOIN post p ON p.oriane_id = rp.oriane_id "
            "JOIN post_processing pp ON pp.oriane_id = p.oriane_id AND pp.raw_id = p.latest_raw_id "
            "AND pp.processor_version = %s "
            "LEFT JOIN claim_extraction ce ON ce.oriane_id = pp.oriane_id AND ce.raw_id = pp.raw_id "
            "AND (ce.extractor_version = %s OR %s) AND ce.politician_id = %s AND ce.status = 'ok' "
            "WHERE rp.politician_id = ANY(%s) AND rp.relevance <> 'low' AND ce.oriane_id IS NULL "
            "AND pp.canonical_oriane_id = pp.oriane_id ORDER BY p.published_at DESC, pp.oriane_id",
            (SEGMENTS_VERSION, self.version, self.skip_any_version, politician_id, merged_ids(self.conn, politician_id))).fetchall()
        return [row for row in rows
                if int(hashlib.sha256(row["oriane_id"].encode()).hexdigest(), 16) % count == index]

    def run(self, politician_id: str, max_posts: int, shard: tuple[int, int] = (0, 1)) -> dict:
        snap = read_person(self.graph, politician_id)
        if snap is None:
            raise LookupError(f"{politician_id} not in System A")
        todo = self.pending(politician_id, shard)
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

    def regrade(self, politician_id: str, from_checks: str) -> dict:
        old_version = extractor_version(self.llm.model, self.taxonomy, from_checks)
        if old_version == self.version:
            raise ValueError("from_checks matches the current checks version")
        snap = read_person(self.graph, politician_id)
        if snap is None:
            raise LookupError(f"{politician_id} not in System A")
        rows = self.conn.execute(
            "SELECT DISTINCT pp.oriane_id, pp.raw_id, pp.canonical_oriane_id, p.published_at, p.platform, "
            "pp.uploader_handle, pp.low_information, previous.response FROM run_post rp "
            "JOIN post p ON p.oriane_id = rp.oriane_id "
            "JOIN post_processing pp ON pp.oriane_id = p.oriane_id AND pp.raw_id = p.latest_raw_id "
            "AND pp.processor_version = %s "
            "JOIN claim_extraction previous ON previous.oriane_id = pp.oriane_id AND previous.raw_id = pp.raw_id "
            "AND previous.extractor_version = %s AND previous.politician_id = %s AND previous.status = 'ok' "
            "AND previous.response IS NOT NULL "
            "LEFT JOIN claim_extraction current_extraction ON current_extraction.oriane_id = pp.oriane_id "
            "AND current_extraction.raw_id = pp.raw_id AND current_extraction.extractor_version = %s "
            "AND current_extraction.politician_id = %s AND current_extraction.status = 'ok' "
            "WHERE rp.politician_id = ANY(%s) AND rp.relevance <> 'low' "
            "AND current_extraction.oriane_id IS NULL "
            "AND pp.canonical_oriane_id = pp.oriane_id ORDER BY p.published_at DESC, pp.oriane_id",
            (SEGMENTS_VERSION, old_version, politician_id, self.version, politician_id,
             merged_ids(self.conn, politician_id))).fetchall()
        result = {"regraded": 0, "claims": 0, "grounding": {}}
        for row in rows:
            response = row.pop("response")
            extracted = self.extract_post(snap, row, response=response, usage={})
            if extracted["status"] != "ok":
                continue
            result["regraded"] += 1
            result["claims"] += extracted["claims"]
            for grounding, count in extracted.get("grounding", {}).items():
                result["grounding"][grounding] = result["grounding"].get(grounding, 0) + count
        return result

    def extract_post(self, snap, row: dict, response=None, usage=None) -> dict:
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
        refs = {}
        if not segs:
            usage = usage or {}
            if response is None:
                claims = []
                response = {"claims": []}
            else:
                claims = response.get("claims") or []
        else:
            prompt, refs = build_prompt(snap, meta, segs, self.taxonomy)
            if response is None:
                try:
                    response, usage = self.llm.extract(SYSTEM_PROMPT, prompt, self.tool)
                except Exception as exc:  # noqa: BLE001 - recorded, run continues with the next post
                    self._log(row, pid, "failed", 0, {}, f"{type(exc).__name__}: {exc}"[:500], now)
                    return {"status": "failed", "claims": 0}
            else:
                usage = usage or {}
            claims = response.get("claims") or []
        with self.conn.transaction():
            for i, c in enumerate(claims):
                g = self._store_claim(row, pid, i, c, refs, speaker, now)
                record["grounding"][g] = record["grounding"].get(g, 0) + 1
            self._log(row, pid, "ok", len(claims), usage, None, now, response)
        record.update(claims=len(claims), input_tokens=usage.get("input_tokens"),
                      output_tokens=usage.get("output_tokens"))
        return record

    def _log(self, row, pid, status, n, usage, error, now, response=None):
        self.conn.execute(
            "INSERT INTO claim_extraction(oriane_id, raw_id, extractor_version, politician_id, model, status, claims, "
            "input_tokens, output_tokens, error, response, extracted_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (oriane_id, raw_id, extractor_version, politician_id) DO UPDATE SET status = EXCLUDED.status, "
            "claims = EXCLUDED.claims, input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens, "
            "error = EXCLUDED.error, response = EXCLUDED.response, extracted_at = EXCLUDED.extracted_at",
            (row["oriane_id"], row["raw_id"], self.version, pid, self.llm.model, status, n,
             usage.get("input_tokens"), usage.get("output_tokens"), error,
             Jsonb(response) if response is not None else None, now))

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
        digits = _numbers(c.get("text") or "", include_words=False)
        if extra := sorted(digits - allowed):
            issues.append(f"paraphrase_numbers_not_in_quote:{','.join(extra)}")
        prop = dict(c.get("proposition") or {})  # keep the stored LLM response unmodified
        predicate = str(prop.get("predicate") or "")
        if not bool(prop.get("negated")) and normalize_text(predicate).startswith(("no ", "nunca ")):
            prop["predicate"] = re.sub(r"^\s*(?:no|nunca)\b[\W_]*", "", predicate, count=1,
                                       flags=re.IGNORECASE).strip()
            prop["negated"] = True
            issues.append("predicate_negation_normalized")
        if not bool(prop.get("negated")) and _negation_targets_predicate(quote, predicate):
            issues.append("proposition_negation_mismatch")
        vals = {n for v in prop.get("values") or [] for n in _value_numbers(v)}
        if vals - _numbers(quote):
            issues.append("proposition_values_not_in_quote")
        quote_negated = _negated(quote)
        if quote_negated != _negated(c.get("text")):
            issues.append("negation_mismatch")
        if bool(prop.get("negated")) and not quote_negated:
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
        if grounding == "exact" and any(issue.partition(":")[0] not in INFO_ISSUES for issue in issues):
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
