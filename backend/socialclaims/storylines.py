"""Storylines: how the same claim is repeated, restated and disputed over time.

Three levels sit above a post:

- statement: one claim taken from one post (a row of `claims.jsonl`);
- version: statements making the same claim with the same figures, place and period, whatever the wording;
- storyline: the versions of one specific claim, so "homicides down 51%", "down 53%" and a denial are three versions
  of one storyline. A storyline is never a topic: sharing a subject area does not put two claims together.

A unit is what gets placed: all statements of one strict proposition (`proposition_id`, see `claims._prop_id`), or a
single statement where the export has no proposition. Strict propositions are never split or rewritten here.

Placing a unit: a deterministic shortlist picks the closest existing storylines of that politician, a judge model
names the relation to them, and the hard rules in `blocked` then refuse any `same_version` whose figures, negation,
place or period differ, whatever the judge said. Answers below the review threshold are not applied: the unit stays
on its own and the proposal waits in the review queue. Every decision is appended to `storyline_decisions.jsonl` with
the judge version, confidence and reason; `storylines.json` holds the current grouping and can be edited back with
`accept`, `reject`, `move`, `detach` and `rename`.

Both files live next to the claims export (`<CLAIMS_EXPORT_DIR>/<ISO2>/`); nothing here touches Postgres or past.dev.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import structlog

from polgraph.ids import normalize_text

log = structlog.get_logger(__name__)

FORMAT = "storylines/1"
RELATIONS = ["same_version", "same_storyline_different_version", "counter_claim", "unrelated"]
PROTECTED = ["values", "negation", "place", "period", "qualifiers"]
SELF_SPEAKERS = {"politician", "politician_quoted"}
STANCES = ("supports", "contradicts", "context")
ONCE = "once"  # the row that collects statements made a single time
STOP = set("""a al algo ante como con contra cual cuando de del desde donde el ella ellos en entre era es esa ese eso esta
este esto fue ha han hay la las le les lo los mas me mi muy no nos o para pero por que se ser si sin sobre su sus
tambien te tiene un una uno y ya the of and to in is was are were be been has have had that this for on with as by
at from it its an or not her his she he they their will would than which who""".split())

SYSTEM_PROMPT = """You group statements about one politician into storylines for a research tool that shows how a \
claim is repeated over time. You are given one new statement and the existing storylines it most resembles.

A storyline is ONE specific claim about ONE specific thing, together with its restatements and the claims that \
dispute it. It is never a topic. Two statements about the same subject area, programme, person or event are \
unrelated unless they assert (or deny) the same thing.

Choose the relation of the new statement to the single closest storyline:
- same_version: the same claim with the same figures, place, reference period and qualifiers as one listed version; \
only the wording differs. Give that version's id.
- same_storyline_different_version: the same specific claim, but a figure, date, place, period or qualifier differs \
from every listed version (for example an updated number).
- counter_claim: it directly denies, disputes or contradicts the storyline's claim.
- unrelated: anything else. When in doubt, choose unrelated.

List in differs_on every field in which the new statement differs from the version or storyline you chose. A \
statement that differs on any of them cannot be same_version.
Always give storyline_label: a short, specific English label (at most 9 words) for the claim the new statement makes, \
naming the thing claimed, not the subject area, and without figures that change between versions. Give \
version_label: at most 8 words saying what marks this version (its figure, date or place). Give confidence from 0 \
to 1 and a one-sentence rationale. Use only ids that appear in the prompt."""


def judge_tool(topics: list[str]) -> dict:
    return {"name": "place_statement", "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["relation", "storyline_id", "version_id", "differs_on", "confidence", "rationale",
                     "storyline_label", "version_label", "topic"],
        "properties": {
            "relation": {"type": "string", "enum": RELATIONS},
            "storyline_id": {"type": "string", "description": "the closest storyline, or '' when unrelated"},
            "version_id": {"type": "string", "description": "the matching version for same_version, else ''"},
            "differs_on": {"type": "array", "items": {"type": "string", "enum": PROTECTED}},
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
            "storyline_label": {"type": "string"},
            "version_label": {"type": "string"},
            "topic": {"type": "string", "enum": topics},
        }}}


def judge_version(model: str, topics: list[str]) -> str:
    h = hashlib.sha256((SYSTEM_PROMPT + json.dumps(judge_tool(topics), sort_keys=True)).encode()).hexdigest()[:12]
    return f"{model}:{h}"


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def unit_key(claim: dict) -> str:
    return f"prop:{claim['proposition_id']}" if claim.get("proposition_id") else f"claim:{claim['claim_id']}"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # split on "\n" only: captions may hold U+2028 and similar, which str.splitlines() treats as line ends
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def build_units(claims: list[dict], propositions: dict[str, dict] | None = None) -> dict[str, list[dict]]:
    """Units per politician, oldest first, so storylines form in the order the claims were made."""
    by_pol: dict[str, dict[str, dict]] = {}
    for c in claims:
        pid = c.get("politician_id")
        if not pid or not (c.get("text") or c.get("canonical_text")):
            continue
        key = unit_key(c)
        u = by_pol.setdefault(pid, {}).setdefault(key, {
            "key": key, "politician_id": pid, "politician_name": c.get("politician_name"),
            "text": c.get("canonical_text") or c.get("text"), "text_en": c.get("text_en"), "type": c.get("type"),
            "first_stated": c.get("published_at"), "statements": 0,
            "facets": (propositions or {}).get(c.get("proposition_id") or "")})
        u["statements"] += 1
        u["text_en"] = u["text_en"] or c.get("text_en")
        if c.get("published_at") and (not u["first_stated"] or c["published_at"] < u["first_stated"]):
            u["first_stated"] = c["published_at"]
    return {pid: sorted(units.values(), key=lambda u: (u["first_stated"] or "", u["key"]))
            for pid, units in by_pol.items()}


# --- hard rules -----------------------------------------------------------------------------------------------------

def _text_numbers(unit: dict) -> list[str]:
    from .claims import _numbers
    return sorted(_numbers(unit.get("text") or "", include_words=False))


def blocked(a: dict, b: dict) -> list[str]:
    """Fields that forbid treating two units as one version. Structured proposition fields are compared when both
    units have them; otherwise the figures written in the two texts are."""
    fa, fb = a.get("facets"), b.get("facets")
    if not (fa and fb):
        return ["values"] if _text_numbers(a) != _text_numbers(b) else []
    from .claims import _value_numbers
    out = []

    def values(f):
        return sorted((_value_numbers(v), normalize_text(v.get("unit") or "")) for v in f.get("vals") or [])
    if values(fa) != values(fb):
        out.append("values")
    if bool(fa.get("negated")) != bool(fb.get("negated")):
        out.append("negation")
    pa, pb = normalize_text(fa.get("place") or ""), normalize_text(fb.get("place") or "")
    if pa and pb and pa != pb:
        out.append("place")
    ra, rb = (fa.get("period_start"), fa.get("period_end")), (fb.get("period_start"), fb.get("period_end"))
    if any(ra) and any(rb) and ra != rb:
        out.append("period")
    return out


# --- shortlist ------------------------------------------------------------------------------------------------------

def _tokens(unit: dict) -> list[str]:
    words = normalize_text(f"{unit.get('text') or ''} {unit.get('text_en') or ''}").split()
    return [w for w in words if len(w) > 2 and w not in STOP and not w.isdigit()]


class Shortlist:
    """TF-IDF cosine over the Spanish and English text, figures left out so restated numbers still match."""

    def __init__(self, units: list[dict]):
        self.df = Counter(t for u in units for t in set(_tokens(u)))
        self.n = max(1, len(units))
        self._vec: dict[str, dict[str, float]] = {}

    def vector(self, unit: dict) -> dict[str, float]:
        v = self._vec.get(unit["key"])
        if v is None:
            tf = Counter(_tokens(unit))
            v = {t: (1 + math.log(n)) * math.log(1 + self.n / (1 + self.df.get(t, 0))) for t, n in tf.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            v = self._vec[unit["key"]] = {t: x / norm for t, x in v.items()}
        return v

    def similarity(self, a: dict, b: dict) -> float:
        va, vb = self.vector(a), self.vector(b)
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(x * vb.get(t, 0.0) for t, x in va.items())


# --- store ----------------------------------------------------------------------------------------------------------

class StorylineStore:
    """`storylines.json` (current grouping per politician) and `storyline_decisions.jsonl` (append-only audit log)."""

    def __init__(self, country_dir: Path):
        self.dir = Path(country_dir)
        self.path, self.log_path = self.dir / "storylines.json", self.dir / "storyline_decisions.jsonl"
        self.lock = threading.RLock()  # politicians are judged on parallel threads; saving serialises all of them
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
            "format": FORMAT, "politicians": {}}

    def politician(self, pid: str) -> dict:
        with self.lock:
            return self.data["politicians"].setdefault(pid, {"storylines": {}, "units": {}, "review": {}})

    def save(self) -> None:
        with self.lock:
            self.data["updated_at"] = _now()
            tmp = self.dir / ".storylines.json.tmp"
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            tmp.replace(self.path)

    def record(self, decision: dict) -> dict:
        decision = {"decision_id": f"sd-{_hash(decision['politician_id'], decision['unit'], _now(), decision['status'])}",
                    "at": _now(), **decision}
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")
        return decision

    # -- edits (used by the engine and by human review) --

    @staticmethod
    def _place(pol: dict, pid: str, unit: str, storyline_id: str | None, version_id: str | None, *, label: str,
               version_label: str, topic: str | None, kind: str, decision: dict) -> tuple[str, str]:
        if storyline_id is None:
            storyline_id = f"sl-{_hash(pid, unit)}"
            pol["storylines"][storyline_id] = {"storyline_id": storyline_id, "label": label, "topic": topic,
                                               "created_at": _now(), "versions": {}}
        versions = pol["storylines"][storyline_id]["versions"]
        if version_id is None:
            version_id = f"sv-{_hash(pid, unit)}"
            versions[version_id] = {"version_id": version_id, "label": version_label, "kind": kind, "units": []}
        versions[version_id]["units"].append(unit)
        pol["units"][unit] = {"storyline_id": storyline_id, "version_id": version_id,
                              **{k: decision.get(k) for k in ("decision_id", "relation", "confidence", "rationale",
                                                              "judge", "status", "rule_blocks")}}
        return storyline_id, version_id

    @staticmethod
    def _remove(pol: dict, unit: str) -> None:
        at = pol["units"].pop(unit, None)
        if not at:
            return
        story = pol["storylines"].get(at["storyline_id"])
        version = (story or {}).get("versions", {}).get(at["version_id"])
        if version:
            version["units"] = [u for u in version["units"] if u != unit]
            if not version["units"]:
                del story["versions"][at["version_id"]]
            if not story["versions"]:
                del pol["storylines"][at["storyline_id"]]


def _proposal(decision: dict) -> dict:
    return {k: decision.get(k) for k in ("decision_id", "unit", "relation", "proposed", "confidence", "rationale",
                                         "judge", "at", "storyline_label", "version_label")}


# --- judge ----------------------------------------------------------------------------------------------------------

def _facet_line(unit: dict) -> str:
    f = unit.get("facets")
    if not f:
        return ""
    vals = "; ".join(f"{v.get('value')} {v.get('unit') or ''}".strip() for v in f.get("vals") or []) or "none"
    period = "–".join(str(x) for x in (f.get("period_start"), f.get("period_end")) if x) or "none"
    quals = "; ".join(f.get("qualifiers") or []) or "none"
    return (f"  fields: figures={vals} | negated={bool(f.get('negated'))} | place={f.get('place') or 'none'} | "
            f"period={period} | qualifiers={quals}\n")


def _unit_lines(unit: dict) -> str:
    en = f"  en: {unit['text_en']}\n" if unit.get("text_en") else ""
    return f"  es: {unit['text']}\n{en}{_facet_line(unit)}"


def build_prompt(unit: dict, detailed: list[dict], catalogue: list[dict], units: dict[str, dict],
                 max_versions: int = 6) -> str:
    """`detailed` and `catalogue` are storyline records; `units` maps unit keys to units for the example texts."""
    lines = [f"POLITICIAN: {unit.get('politician_name') or unit['politician_id']}", "",
             f"NEW STATEMENT (first said {(unit.get('first_stated') or 'unknown')[:10]}, type {unit.get('type')})",
             _unit_lines(unit)]
    lines.append("CANDIDATE STORYLINES (closest first)" if detailed else "CANDIDATE STORYLINES: none yet")
    for s in detailed:
        lines.append(f"[{s['storyline_id']}] \"{s['label']}\" (topic {s.get('topic')})")
        versions = list(s["versions"].values())
        for v in versions[:max_versions]:
            sample = units.get(v["units"][0])
            if not sample:
                continue
            kind = "counter-claim" if v["kind"] == "counter_claim" else "claim"
            lines.append(f" [{v['version_id']}] {kind}, first said {(sample.get('first_stated') or '')[:10]}, "
                         f"{len(v['units'])} wording(s):\n{_unit_lines(sample)}".rstrip())
        if len(versions) > max_versions:
            lines.append(f" (+{len(versions) - max_versions} more versions not shown)")
    if catalogue:
        lines += ["", "OTHER EXISTING STORYLINES (labels only; pick one only if it is clearly the same claim)"]
        lines += [f"[{s['storyline_id']}] \"{s['label']}\"" for s in catalogue]
    return "\n".join(lines)


class StorylineEngine:
    def __init__(self, store: StorylineStore, llm, topics: list[str], threshold: float = 0.75, shortlist: int = 8,
                 catalogue: int = 150):
        self.store, self.llm, self.topics = store, llm, topics
        self.threshold, self.k, self.catalogue = threshold, shortlist, catalogue
        self.tool = judge_tool(topics)
        self.version = judge_version(getattr(llm, "model", "unknown"), topics)

    def pending(self, pid: str, units: list[dict]) -> list[dict]:
        placed = self.store.politician(pid)["units"]
        return [u for u in units if u["key"] not in placed]

    def run(self, pid: str, units: list[dict], max_units: int | None = None) -> dict:
        pol = self.store.politician(pid)
        by_key = {u["key"]: u for u in units}
        ranker = Shortlist(units)
        out = Counter()
        usage = Counter()
        for unit in self.pending(pid, units)[:max_units]:
            try:
                decision = self._decide(pid, pol, unit, by_key, ranker, usage)
            except Exception as exc:  # one failed call must not lose the units already placed
                out["errors"] += 1
                log.warning("storylines.judge_failed", unit=unit["key"], error=f"{type(exc).__name__}: {str(exc)[:200]}")
                continue
            out[decision["status"]] += 1
            out[f"relation:{decision['relation']}"] += 1
            out["rule_blocked"] += bool(decision["rule_blocks"])
            if (out["applied"] + out["needs_review"]) % 25 == 0:
                self.store.save()
        self.store.save()
        return {"politician_id": pid, "judge": self.version, "units": len(units),
                "pending": len(self.pending(pid, units)), "storylines": len(pol["storylines"]),
                "needs_review": len(pol["review"]), **out, "usage": dict(usage)}

    def _ask(self, unit, detailed, catalogue, by_key, usage) -> dict:
        verdict, used = self.llm.extract(SYSTEM_PROMPT, build_prompt(unit, detailed, catalogue, by_key), self.tool)
        usage["calls"] += 1
        for k in ("input_tokens", "output_tokens"):
            usage[k] += (used or {}).get(k) or 0
        return verdict

    def _decide(self, pid: str, pol: dict, unit: dict, by_key: dict, ranker: Shortlist, usage: Counter) -> dict:
        def score(story: dict) -> float:
            return max((ranker.similarity(unit, by_key[k]) for v in story["versions"].values() for k in v["units"]
                        if k in by_key), default=0.0)
        ranked = sorted(pol["storylines"].values(), key=lambda s: (-score(s), s["storyline_id"]))
        detailed, rest = ranked[:self.k], ranked[self.k:self.k + self.catalogue]
        verdict = self._ask(unit, detailed, rest, by_key, usage)
        if verdict.get("relation") != "unrelated" and verdict.get("storyline_id") in {s["storyline_id"] for s in rest}:
            # Picked from the label-only list: ask again with that storyline's versions in view.
            detailed = [pol["storylines"][verdict["storyline_id"]]]
            verdict = self._ask(unit, detailed, [], by_key, usage)

        relation = verdict.get("relation") if verdict.get("relation") in RELATIONS else "unrelated"
        story = pol["storylines"].get(verdict.get("storyline_id") or "") if relation != "unrelated" else None
        blocks: list[str] = []
        if relation != "unrelated" and story not in detailed:
            relation, story, blocks = "unrelated", None, ["unknown_storyline"]
        version = story["versions"].get(verdict.get("version_id") or "") if story and relation == "same_version" else None
        if relation == "same_version":
            if version is None:
                blocks.append("unknown_version")
            else:
                blocks += sorted({f for k in version["units"] if k in by_key for f in blocked(unit, by_key[k])}
                                 | {f for f in verdict.get("differs_on") or [] if f in PROTECTED})
            if blocks:  # the hard rule: never one version when a protected field differs
                relation, version = "same_storyline_different_version", None
        confidence = max(0.0, min(1.0, float(verdict.get("confidence") or 0.0)))
        review = relation != "unrelated" and confidence < self.threshold
        label = (verdict.get("storyline_label") or "").strip() or (unit.get("text_en") or unit["text"])[:90]
        version_label = (verdict.get("version_label") or "").strip()
        decision = self.store.record({
            "politician_id": pid, "unit": unit["key"], "relation": relation, "judge_relation": verdict.get("relation"),
            "confidence": confidence, "rationale": verdict.get("rationale"), "differs_on": verdict.get("differs_on") or [],
            "rule_blocks": blocks, "status": "needs_review" if review else "applied", "judge": self.version,
            "storyline_label": label, "version_label": version_label, "topic": verdict.get("topic"),
            "proposed": {"storyline_id": story["storyline_id"] if story else None,
                         "version_id": version["version_id"] if version else None}})
        kind = "counter_claim" if relation == "counter_claim" else "claim"
        with self.store.lock:
            if review or story is None:
                self.store._place(pol, pid, unit["key"], None, None, label=label, version_label=version_label,
                                  topic=verdict.get("topic"), kind="claim", decision=decision)
                if review:
                    pol["review"][decision["decision_id"]] = _proposal(decision)
            else:
                self.store._place(pol, pid, unit["key"], story["storyline_id"],
                                  version["version_id"] if version else None, label=label,
                                  version_label=version_label, topic=verdict.get("topic"), kind=kind, decision=decision)
        return decision


def run_country(engine: StorylineEngine, units_by_pol: dict[str, list[dict]], politician_ids: list[str] | None = None,
                max_units: int | None = None, workers: int = 4) -> list[dict]:
    """Politicians are judged in parallel; each politician's units one after another, since every placement depends
    on the storylines the earlier ones opened."""
    ids = politician_ids or sorted(units_by_pol)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(lambda pid: engine.run(pid, units_by_pol.get(pid, []), max_units), ids))


# --- human review ---------------------------------------------------------------------------------------------------

class ReviewError(ValueError):
    pass


def _find(store: StorylineStore, decision_id: str) -> tuple[str, dict, dict]:
    for pid, pol in store.data["politicians"].items():
        if decision_id in pol["review"]:
            return pid, pol, pol["review"][decision_id]
    raise ReviewError(f"no pending proposal {decision_id}")


def _human(store: StorylineStore, pid: str, unit: str, action: str, **extra) -> dict:
    return store.record({"politician_id": pid, "unit": unit, "relation": extra.pop("relation", None), "status": action,
                         "judge": "human", "confidence": 1.0, "rule_blocks": [], **extra})


def review_queue(store: StorylineStore, units_by_pol: dict[str, list[dict]]) -> list[dict]:
    out = []
    for pid, pol in sorted(store.data["politicians"].items()):
        texts = {u["key"]: u for u in units_by_pol.get(pid, [])}
        for p in pol["review"].values():
            target = pol["storylines"].get((p.get("proposed") or {}).get("storyline_id") or "") or {}
            unit = texts.get(p["unit"]) or {}
            out.append({**p, "politician_id": pid, "text": unit.get("text"), "text_en": unit.get("text_en"),
                        "proposed_storyline_label": target.get("label")})
    return sorted(out, key=lambda p: p.get("confidence") or 0.0, reverse=True)


def accept(store: StorylineStore, decision_id: str, units_by_pol: dict[str, list[dict]]) -> dict:
    """Apply a queued proposal. The hard rules still hold: a blocked `same_version` becomes a version of its own."""
    pid, _, p = _find(store, decision_id)
    return move(store, pid, p["unit"], (p.get("proposed") or {}).get("storyline_id"),
                (p.get("proposed") or {}).get("version_id"), units_by_pol, relation=p["relation"], of=decision_id,
                version_label=p.get("version_label") or "")


def reject(store: StorylineStore, decision_id: str) -> dict:
    pid, pol, p = _find(store, decision_id)
    del pol["review"][decision_id]
    decision = _human(store, pid, p["unit"], "rejected", of=decision_id)
    store.save()
    return decision


def move(store: StorylineStore, pid: str, unit: str, storyline_id: str | None, version_id: str | None,
         units_by_pol: dict[str, list[dict]], relation: str | None = None, of: str | None = None,
         version_label: str = "", label: str | None = None) -> dict:
    """Put a unit into a storyline (`storyline_id=None` opens a new one), as a human decision."""
    pol = store.politician(pid)
    by_key = {u["key"]: u for u in units_by_pol.get(pid, [])}
    if unit not in by_key and unit not in pol["units"]:
        raise ReviewError(f"unknown unit {unit}")
    story = pol["storylines"].get(storyline_id or "")
    if storyline_id and not story:
        raise ReviewError(f"unknown storyline {storyline_id}")
    version = (story or {}).get("versions", {}).get(version_id or "")
    if version_id and not version:
        raise ReviewError(f"unknown version {version_id} in {storyline_id}")
    blocks = sorted({f for k in (version or {}).get("units", []) if k in by_key and unit in by_key and k != unit
                     for f in blocked(by_key[unit], by_key[k])})
    if blocks:
        version, version_id = None, None
    if relation is None or blocks:
        relation = ("same_version" if version else "same_storyline_different_version") if story else "unrelated"
    was = pol["units"].get(unit) or {}
    old_story = pol["storylines"].get(was.get("storyline_id") or "") or {}
    if story and not any(k != unit for v in story["versions"].values() for k in v["units"]):
        raise ReviewError(f"{unit} is the only member of {storyline_id}")
    if version and version["units"] == [unit]:
        version, version_id = None, None
    store._remove(pol, unit)
    for did in [d for d, p in pol["review"].items() if p["unit"] == unit]:
        del pol["review"][did]
    decision = _human(store, pid, unit, "accepted" if of else "moved", relation=relation, of=of, rule_blocks=blocks,
                      proposed={"storyline_id": storyline_id, "version_id": version_id})
    text = by_key.get(unit) or {}
    store._place(pol, pid, unit, storyline_id, version_id if version else None,
                 label=label or old_story.get("label") or (text.get("text_en") or text.get("text") or unit)[:90],
                 version_label=version_label, topic=old_story.get("topic"),
                 kind="counter_claim" if relation == "counter_claim" else "claim", decision=decision)
    store.save()
    return decision


def rename(store: StorylineStore, storyline_id: str, label: str) -> dict:
    for pid, pol in store.data["politicians"].items():
        if storyline_id in pol["storylines"]:
            old = pol["storylines"][storyline_id]["label"]
            pol["storylines"][storyline_id]["label"] = label
            decision = _human(store, pid, storyline_id, "renamed", old_label=old, storyline_label=label)
            store.save()
            return decision
    raise ReviewError(f"unknown storyline {storyline_id}")


# --- read side: the week grid and the detail under it ---------------------------------------------------------------

def week_of(iso: str | None) -> str | None:
    """Monday (UTC) of the ISO week a timestamp falls in."""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    day = (d.astimezone(timezone.utc) if d.tzinfo else d).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _week_range(first: str, last: str) -> list[str]:
    out, d, end = [], date.fromisoformat(first), date.fromisoformat(last)
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=7)
    return out


def _side(claim: dict) -> str:
    return "self" if claim.get("speaker") in SELF_SPEAKERS else "others"


def _cells(statements: list[dict]) -> dict[str, dict]:
    """Per week: statements and distinct posts, as [all, said by the politician, said by others]."""
    cells: dict[str, dict] = {}
    for c in statements:
        w = c["_week"]
        if not w:
            continue
        cell = cells.setdefault(w, {"statements": [0, 0, 0], "_posts": [set(), set(), set()]})
        i = 1 if _side(c) == "self" else 2
        cell["statements"][0] += 1
        cell["statements"][i] += 1
        cell["_posts"][0].add(c.get("data_point_id"))
        cell["_posts"][i].add(c.get("data_point_id"))
    return {w: {"statements": c["statements"], "posts": [len(s) for s in c["_posts"]]} for w, c in cells.items()}


def group(claims: list[dict], pol: dict | None) -> dict[str, dict]:
    """Every statement of one politician under its storyline and version. Units the judge has not placed yet are
    storylines of their own, so nothing is hidden while grouping is incomplete."""
    pol = pol or {"storylines": {}, "units": {}, "review": {}}
    out: dict[str, dict] = {}
    for c in claims:
        key = unit_key(c)
        at = pol["units"].get(key)
        story = pol["storylines"].get(at["storyline_id"]) if at else None
        version = story["versions"].get(at["version_id"]) if story else None
        if not version:
            sid = vid = f"u-{_hash(c.get('politician_id') or '', key)}"
            story = {"storyline_id": sid, "label": c.get("text_en") or c.get("text"), "topic": None}
            version = {"version_id": vid, "label": "", "kind": "claim"}
            at = None
        s = out.setdefault(story["storyline_id"], {**{k: story.get(k) for k in ("storyline_id", "label", "topic")},
                                                   "judged": at is not None, "versions": {}, "statements": []})
        v = s["versions"].setdefault(version["version_id"], {**{k: version.get(k) for k in ("version_id", "label", "kind")},
                                                            "statements": []})
        row = {**c, "_week": week_of(c.get("published_at")), "_unit": key, "_decision": at}
        v["statements"].append(row)
        s["statements"].append(row)
    return out


def _summary(story: dict, review_units: set[str]) -> dict:
    rows = story["statements"]
    cells = _cells(rows)
    dates = sorted(c["published_at"] for c in rows if c.get("published_at"))
    return {"storyline_id": story["storyline_id"], "label": story["label"], "topic": story["topic"],
            "judged": story["judged"], "statements": len(rows), "posts": len({c.get("data_point_id") for c in rows}),
            "versions": len(story["versions"]), "weeks_active": len(cells),
            "counter_claims": sum(1 for v in story["versions"].values() if v["kind"] == "counter_claim"),
            "needs_review": sum(1 for c in rows if c["_unit"] in review_units),
            "accounts": len({c.get("uploader_handle") for c in rows if c.get("uploader_handle")}),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None, "cells": cells}


def _evidence_rows(statements: list[dict], evidence: dict) -> list[dict]:
    """The evidence behind a set of statements, each (proposition, politician) counted once."""
    keys = dict.fromkeys((c.get("proposition_id") or "", c.get("politician_id") or "") for c in statements)
    return [e for k in keys for e in evidence.get(k, [])]


def matrix(claims: list[dict], pol: dict | None, evidence: dict | None = None) -> dict:
    """The week grid: one row per storyline said more than once, plus one row for everything said once."""
    stories = group(claims, pol)
    review_units = {p["unit"] for p in (pol or {}).get("review", {}).values()}
    counts = {sid: stance_counts(_evidence_rows(s["statements"], evidence or {})) for sid, s in stories.items()}
    rows = [{**_summary(s, review_units), "evidence_counts": counts[s["storyline_id"]]}
            for s in stories.values() if len(s["statements"]) > 1]
    rows.sort(key=lambda r: (-r["statements"], r["first"] or "", r["storyline_id"]))
    once = [c for s in stories.values() if len(s["statements"]) == 1 for c in s["statements"]]
    if once:
        rows.append({**_summary({"storyline_id": ONCE, "label": "Statements made only once", "topic": None,
                                 "judged": True, "versions": {}, "statements": once}, review_units),
                     "versions": len(once), "counter_claims": 0,
                     "evidence_counts": stance_counts(_evidence_rows(once, evidence or {}))})
    weeks = sorted({w for r in rows for w in r["cells"]})
    placed = (pol or {}).get("units", {})
    units = {unit_key(c) for c in claims}
    return {"weeks": _week_range(weeks[0], weeks[-1]) if weeks else [],
            "topics": sorted({r["topic"] for r in rows if r["topic"]}),
            "totals": {"statements": len(claims), "posts": len({c.get("data_point_id") for c in claims}),
                       "storylines": sum(1 for r in rows if r["storyline_id"] != ONCE),
                       "recurring": sum(1 for r in rows if r["storyline_id"] != ONCE and r["weeks_active"] > 1),
                       "with_post_link": sum(1 for c in claims if c.get("url"))},
            "grouping": {"units": len(units), "placed": len(units & set(placed)),
                         "needs_review": len((pol or {}).get("review", {})),
                         "judges": sorted({u.get("judge") for k, u in placed.items() if k in units and u.get("judge")})},
            "storylines": rows}


def evidence_index(links: list[dict], documents: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Evidence per (proposition, politician), each link carrying its document; official sources first."""
    docs = {d["document_datapoint"]: d for d in documents}
    out: dict[tuple[str, str], list[dict]] = {}
    for link in links:
        row = {k: link.get(k) for k in ("link_id", "stance", "confidence", "quote", "rationale", "assessed_at",
                                        "mapper_version", "authority_tier", "source_class")}
        row["document"] = docs.get(link.get("document_datapoint") or "")
        out.setdefault((link.get("proposition_id") or "", link.get("politician_id") or ""), []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r.get("authority_tier") or 9, -(r.get("confidence") or 0.0), r.get("link_id") or ""))
    return out


def stance_counts(evidence: list[dict]) -> dict[str, int]:
    return {s: sum(1 for e in evidence if e.get("stance") == s) for s in STANCES}


def _statement(c: dict, evidence: dict) -> dict:
    ev = evidence.get((c.get("proposition_id") or "", c.get("politician_id") or ""), [])
    d = c["_decision"] or {}
    return {**{k: c.get(k) for k in ("claim_id", "text", "text_en", "evidence_span", "type", "speaker",
                                     "relation_to_politician", "about_politician", "proposition_id",
                                     "canonical_text", "published_at")},
            "week": c["_week"], "unit": c["_unit"],
            "placement": {k: d.get(k) for k in ("relation", "confidence", "rationale", "judge", "status",
                                                "rule_blocks", "decision_id")} if d else None,
            "evidence": ev, "evidence_counts": stance_counts(ev)}


def detail(claims: list[dict], posts: dict[str, dict], pol: dict | None, evidence: dict, storyline_id: str,
           week: str | None = None) -> dict | None:
    """One storyline in full: versions -> posts -> statements -> evidence documents. `week` keeps one week only."""
    stories = group(claims, pol)
    if storyline_id == ONCE:
        versions = [{"version_id": s["storyline_id"], "label": "", "kind": "claim", "statements": s["statements"]}
                    for s in stories.values() if len(s["statements"]) == 1]
        story = {"storyline_id": ONCE, "label": "Statements made only once", "topic": None, "judged": True}
    elif storyline_id in stories:
        story = stories[storyline_id]
        versions = list(story["versions"].values())
    else:
        return None
    review = (pol or {}).get("review", {})
    out_versions, all_rows = [], []
    for v in versions:
        rows = [c for c in v["statements"] if week is None or c["_week"] == week]
        if not rows:
            continue
        all_rows += rows
        by_post: dict[str, list[dict]] = {}
        for c in sorted(rows, key=lambda c: (c.get("published_at") or "", c.get("claim_id") or "")):
            by_post.setdefault(c.get("data_point_id") or "", []).append(c)
        post_rows = []
        for dp, cs in by_post.items():
            p = posts.get(dp) or {}
            post_rows.append({
                "data_point_id": dp, "published_at": p.get("published_at") or cs[0].get("published_at"),
                "platform": p.get("platform") or cs[0].get("platform"), "url": p.get("url") or cs[0].get("url"),
                "uploader_handle": p.get("uploader_handle") or cs[0].get("uploader_handle"),
                "uploader_name": p.get("uploader_name") or cs[0].get("uploader_name"),
                "platform_id": p.get("platform_id"), "oriane_content_id": p.get("oriane_content_id"),
                "format": p.get("format"), "caption": p.get("caption"), "engagement": p.get("engagement") or {},
                "statements": [_statement(c, evidence) for c in cs]})
        every = v["statements"]
        dates = sorted(c["published_at"] for c in every if c.get("published_at"))
        out_versions.append({"version_id": v["version_id"], "label": v["label"], "kind": v["kind"],
                             "text": every[0].get("canonical_text") or every[0].get("text"),
                             "text_en": every[0].get("text_en"), "first_seen": dates[0] if dates else None,
                             "statements": len(rows), "posts": len(by_post), "cells": _cells(every),
                             "post_list": post_rows})
    out_versions.sort(key=lambda v: (v["first_seen"] or "", v["version_id"]))
    ev_rows = _evidence_rows(all_rows, evidence)
    units = {c["_unit"] for c in all_rows}
    return {"storyline_id": story["storyline_id"], "label": story["label"], "topic": story["topic"],
            "judged": story["judged"], "week": week, "statements": len(all_rows),
            "posts": len({c.get("data_point_id") for c in all_rows}),
            "speakers": dict(Counter(c.get("speaker") or "other" for c in all_rows).most_common()),
            "accounts": sorted({c["uploader_handle"] for c in all_rows if c.get("uploader_handle")}),
            "cells": _cells(all_rows if week else [c for v in versions for c in v["statements"]]),
            "evidence_counts": stance_counts(ev_rows),
            "documents": len({(e.get("document") or {}).get("document_datapoint") or e.get("link_id") for e in ev_rows}),
            "pending_review": [p for p in review.values() if p["unit"] in units],
            "versions": out_versions}
