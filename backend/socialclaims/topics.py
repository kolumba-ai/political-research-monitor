"""Topics: the concrete subjects a politician's storylines are about.

A topic sits between a storyline (one specific claim) and a taxonomy subject area ("seguridad"): one programme,
project, case, event, institution or policy issue that several storylines share, such as "Passenger train lines".
Topics only group storylines for the week grid; they never merge, split or rewrite a storyline.

A judge model reads one politician's storylines without a topic and answers with the topics they fall under,
reusing the existing ones.
`storylines.json` holds the result (`topics` per politician, `topic_id` on each storyline; a storyline's `topic` field
is its taxonomy subject area), every assignment is appended to `storyline_decisions.jsonl`, and `set_topic` /
`rename_topic` are the human edits.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import structlog

from polgraph.ids import normalize_text

from .storylines import ReviewError, StorylineStore, _hash, _human, _now

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You organise the storylines about one politician into topics, for a research tool that shows \
which subjects come back over time. A storyline is one specific claim. A topic is the subject that several \
storylines are about: one programme, project, policy issue, institution, case, scandal or event.

The right size for a topic is the one a journalist would file a series of stories under, for example:
- "Passenger train lines": lines opened, kilometres built, investment, jobs created, progress of each line.
- "Public hospitals and clinics": new hospitals, one named hospital, consultations, waiting times, doctors hired.
- "Jobs and wages": the minimum wage, employment records, unemployment, working hours.
- "INE contracts case": every allegation, denial and development in that one case.
A topic is narrower than a whole subject area such as security, health, the economy or corruption, and broader \
than a single claim.

How to work: read the whole list first and find the subjects that come up more than once, then build the topics \
around them. Most storylines should end up in a topic with other storylines.
- Reuse an existing topic whenever a storyline fits it; never open a second topic for the same subject.
- Put storylines together when they are about the same programme, project, service, institution, case, person's \
affair or event, even if their claims differ. Do not put them together only because they share a subject area.
- A storyline gets a topic of its own only when no other storyline is about its subject; name that topic after \
the subject, not after the claim, so that later storylines can join it.
- Labels are English noun phrases of 2 to 6 words naming the subject. No figures and no verdicts. Use a person's \
name only when the subject is that person's own case or affair. Never use a catch-all such as "Other".
Answer with the topics, each with the ids of its storylines. Place every listed storyline exactly once, using only \
ids that appear in the prompt."""

TOOL = {"name": "assign_topics", "input_schema": {
    "type": "object", "additionalProperties": False, "required": ["topics"],
    "properties": {"topics": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["topic_id", "label", "storyline_ids"],
        "properties": {
            "topic_id": {"type": "string", "description": "an existing topic's id, or '' for a new topic"},
            "label": {"type": "string", "description": "the topic's label (new or existing)"},
            "storyline_ids": {"type": "array", "items": {"type": "string"},
                              "description": "the listed storylines that belong to this topic"}}}}}}}


def judge_version(model: str) -> str:
    h = hashlib.sha256((SYSTEM_PROMPT + json.dumps(TOOL, sort_keys=True)).encode()).hexdigest()[:12]
    return f"{model}:{h}"


def _topics(pol: dict) -> dict:
    return pol.setdefault("topics", {})


def _resolve(pol: dict, pid: str, topic: str, seed: str) -> tuple[dict, bool]:
    """The topic with this id or (normalised) label, opened when there is none."""
    topics = _topics(pol)
    if topic in topics:
        return topics[topic], False
    label = " ".join(topic.split())
    for t in topics.values():
        if normalize_text(t["label"]) == normalize_text(label):
            return t, False
    tid = f"tp-{_hash(pid, seed, label)}"
    topics[tid] = {"topic_id": tid, "label": label, "created_at": _now()}
    return topics[tid], True


def _prune(pol: dict) -> None:
    used = {s.get("topic_id") for s in pol["storylines"].values()}
    for tid in [t for t in _topics(pol) if t not in used]:
        del pol["topics"][tid]


def _size(story: dict, by_key: dict) -> int:
    return sum((by_key.get(k) or {}).get("statements", 1) for v in story["versions"].values() for k in v["units"])


SECOND_LOOK = ("NOTE: each storyline below was left alone in a topic of its own. Look at them again: put each one "
               "under an existing topic it fits, or together with other storylines of this list that share its "
               "subject. Leave one alone only if nothing else is about its subject.")


def build_prompt(name: str, pol: dict, todo: list[dict], by_key: dict, members: int = 6, note: str = "") -> str:
    lines = [f"POLITICIAN: {name}", ""] + ([note, ""] if note else [])
    topics = _topics(pol)
    if topics:
        lines.append("EXISTING TOPICS")
        for t in topics.values():
            labels = [s["label"] for s in pol["storylines"].values() if s.get("topic_id") == t["topic_id"]]
            shown = "; ".join(f"\"{x[:80]}\"" for x in labels[:members]) + (" …" if len(labels) > members else "")
            lines.append(f"[{t['topic_id']}] \"{t['label']}\" ({len(labels)} storylines): {shown}")
        lines.append("")
    lines.append("STORYLINES TO PLACE")
    for s in todo:
        unit = next((by_key[k] for v in s["versions"].values() for k in v["units"] if k in by_key), {})
        text = (unit.get("text_en") or unit.get("text") or "").strip()
        example = "" if not text or text[:40] == (s["label"] or "")[:40] else f" | e.g. {text[:200]}"
        lines.append(f"[{s['storyline_id']}] \"{(s['label'] or '')[:160]}\" | area {s.get('topic') or 'none'} | "
                     f"{_size(s, by_key)} statement(s){example}")
    return "\n".join(lines)


class TopicEngine:
    def __init__(self, store: StorylineStore, llm, batch: int = 150, rounds: int = 3):
        self.store, self.llm, self.batch, self.rounds = store, llm, batch, rounds
        self.version = judge_version(getattr(llm, "model", "unknown"))

    def pending(self, pid: str) -> list[dict]:
        pol = self.store.politician(pid)
        return [s for s in pol["storylines"].values() if s.get("topic_id") not in _topics(pol)]

    def run(self, pid: str, units: list[dict]) -> dict:
        pol = self.store.politician(pid)
        by_key = {u["key"]: u for u in units}
        name = next((u.get("politician_name") for u in units if u.get("politician_name")), pid)
        out, usage = Counter(), Counter()
        for _ in range(self.rounds):  # a storyline the judge leaves out is asked again in the next round
            todo = sorted(self.pending(pid), key=lambda s: (-_size(s, by_key), s.get("created_at") or "", s["storyline_id"]))
            placed = 0
            for i in range(0, len(todo), self.batch):
                chunk = todo[i:i + self.batch]
                try:
                    verdict, used = self.llm.extract(SYSTEM_PROMPT, build_prompt(name, pol, chunk, by_key), TOOL)
                except Exception as exc:  # noqa: BLE001 - one failed call must not lose the topics already assigned
                    out["errors"] += 1
                    log.warning("topics.judge_failed", politician_id=pid, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                    continue
                usage["calls"] += 1
                for k in ("input_tokens", "output_tokens"):
                    usage[k] += (used or {}).get(k) or 0
                placed += self._apply(pid, pol, chunk, verdict, out)
                self.store.save()
            if not placed:
                break
        if out["assigned"]:
            self._second_look(pid, pol, name, by_key, out, usage)
        with self.store.lock:
            _prune(pol)
        self.store.save()
        return {"politician_id": pid, "judge": self.version, "storylines": len(pol["storylines"]),
                "topics": len(_topics(pol)), "pending": len(self.pending(pid)), **out, "usage": dict(usage)}

    def _second_look(self, pid: str, pol: dict, name: str, by_key: dict, out: Counter, usage: Counter) -> None:
        """Ask once more about the storylines that ended up alone in a topic: a list read in one go leaves many of
        them out of the group they belong to. One that stays alone keeps the topic it had."""
        with self.store.lock:
            sizes = Counter(s.get("topic_id") for s in pol["storylines"].values())
            alone = [s for s in pol["storylines"].values() if sizes[s.get("topic_id")] == 1]
            if len(alone) < 2:
                return
            before = {s["storyline_id"]: pol["topics"][s["topic_id"]] for s in alone}
            for s in alone:
                del pol["topics"][s.pop("topic_id")]
        alone.sort(key=lambda s: (s.get("topic") or "", s.get("created_at") or "", s["storyline_id"]))
        for i in range(0, len(alone), self.batch):
            chunk = alone[i:i + self.batch]
            try:
                verdict, used = self.llm.extract(SYSTEM_PROMPT, build_prompt(name, pol, chunk, by_key, note=SECOND_LOOK), TOOL)
            except Exception as exc:  # noqa: BLE001 - the first grouping stands
                out["errors"] += 1
                log.warning("topics.judge_failed", politician_id=pid, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                continue
            usage["calls"] += 1
            for k in ("input_tokens", "output_tokens"):
                usage[k] += (used or {}).get(k) or 0
            self._apply(pid, pol, chunk, verdict, Counter())
        with self.store.lock:
            for s in alone:
                old = before[s["storyline_id"]]
                if s.get("topic_id") not in pol["topics"]:  # not answered: back to the topic it had
                    s["topic_id"] = old["topic_id"]
                    pol["topics"][old["topic_id"]] = old
            sizes = Counter(s["topic_id"] for s in pol["storylines"].values())
            out["alone"] = sum(1 for n in sizes.values() if n == 1)
            out["regrouped"] = sum(1 for s in alone if sizes[s["topic_id"]] > 1)
        self.store.save()

    def _apply(self, pid: str, pol: dict, chunk: list[dict], verdict: dict, out: Counter) -> int:
        todo = {s["storyline_id"]: s for s in chunk}
        placed = 0
        answers = [(t, sid) for t in verdict.get("topics") or [] for sid in t.get("storyline_ids") or []]
        for t, sid in answers:
            story = todo.pop(sid, None)  # a second mention of a storyline is ignored: the first topic keeps it
            wanted = t.get("topic_id") if t.get("topic_id") in _topics(pol) else (t.get("label") or "").strip()
            if not story or not wanted:
                continue
            with self.store.lock:
                topic, opened = _resolve(pol, pid, wanted, story["storyline_id"])
                story["topic_id"] = topic["topic_id"]
            self.store.record({"politician_id": pid, "unit": story["storyline_id"], "status": "topic_assigned",
                               "relation": None, "judge": self.version, "topic_id": topic["topic_id"],
                               "topic_label": topic["label"], "new_topic": opened})
            out["assigned"] += 1
            out["opened"] += opened
            placed += 1
        return placed


def reset(store: StorylineStore, pid: str) -> None:
    """Forget a politician's topics, so the judge groups every storyline again."""
    pol = store.politician(pid)
    with store.lock:
        pol["topics"] = {}
        for s in pol["storylines"].values():
            s.pop("topic_id", None)
    store.save()


def run_country(engine: TopicEngine, units_by_pol: dict[str, list[dict]], politician_ids: list[str] | None = None,
                workers: int = 4) -> list[dict]:
    ids = politician_ids or sorted(engine.store.data["politicians"])
    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(lambda pid: engine.run(pid, units_by_pol.get(pid, [])), ids))


# --- human edits ----------------------------------------------------------------------------------------------------

def _story(store: StorylineStore, storyline_id: str) -> tuple[str, dict, dict]:
    for pid, pol in store.data["politicians"].items():
        if storyline_id in pol["storylines"]:
            return pid, pol, pol["storylines"][storyline_id]
    raise ReviewError(f"unknown storyline {storyline_id}")


def set_topic(store: StorylineStore, storyline_id: str, topic: str) -> dict:
    """Move a storyline to a topic, given by id or label; an unknown label opens a new topic."""
    pid, pol, story = _story(store, storyline_id)
    if not topic.strip() or (topic.startswith("tp-") and topic not in _topics(pol)):
        raise ReviewError(f"unknown topic {topic!r}")
    target, opened = _resolve(pol, pid, topic.strip(), storyline_id)
    old, story["topic_id"] = story.get("topic_id"), target["topic_id"]
    _prune(pol)
    decision = _human(store, pid, storyline_id, "topic_set", topic_id=target["topic_id"], topic_label=target["label"],
                      new_topic=opened, old_topic_id=old)
    store.save()
    return decision


def rename_topic(store: StorylineStore, topic_id: str, label: str) -> dict:
    for pid, pol in store.data["politicians"].items():
        if topic_id in _topics(pol):
            old, pol["topics"][topic_id]["label"] = pol["topics"][topic_id]["label"], " ".join(label.split())
            decision = _human(store, pid, topic_id, "topic_renamed", old_label=old, topic_label=pol["topics"][topic_id]["label"])
            store.save()
            return decision
    raise ReviewError(f"unknown topic {topic_id}")
