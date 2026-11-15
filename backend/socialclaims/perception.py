"""Perception labels for captured posts: how a post presents a politician, and how its top comments respond.

One model call per politician and post reads the caption, transcript, on-screen text and the stored top comments, and
returns the presentation label and each comment's stance. Results go to `post_presentation` and `comment_stance`
(migration 007); `metrics-export` turns them into the explorer's indices. Billed unless `--dry-run`.

A comment counts as a likely bot only on behavioural evidence: the same text under 3 or more posts. System B does
not keep commenters' handles, so the handle-based signal other monitors use is not available here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

PROMPT_VERSION = "perception-1"
SPEECH_CHARS, ONSCREEN_CHARS, CAPTION_CHARS, COMMENT_CHARS, MAX_COMMENTS = 6000, 1500, 2200, 300, 15
BOT_MIN_POSTS, BOT_MIN_CHARS = 3, 12
CHARS_PER_TOKEN = 3.2          # Spanish text; used only for the dry-run estimate
SYSTEM_TOKENS, OUTPUT_BASE_TOKENS, OUTPUT_TOKENS_PER_COMMENT = 420, 70, 16

SYSTEM_PROMPT = """You label social media posts for a political research monitor. You are given one politician and \
one post that mentions them or comes from their account, with its most-liked comments.

1. relevant: false only if the post is not really about this politician (a namesake, a passing tag, an unrelated \
hashtag). Otherwise true.
2. presentation: how the post itself presents the politician to its audience.
   positive: promotes, praises or favourably showcases them or their work.
   negative: criticises, accuses, mocks or unfavourably frames them.
   neutral: reports or mentions them without a clear favourable or unfavourable framing. Use neutral when not relevant.
3. reason: at most 12 words, in English, saying what in the post decides the label.
4. comments: for every numbered comment, its stance toward the politician.
   for: supports, praises or defends the politician. against: criticises, insults or opposes the politician.
   neutral: about something else, a question, or no clear stance toward the politician.
   generic: true if the comment is a stock phrase, emoji string or slogan with no content of its own.

Judge the text you are given; do not use outside knowledge about the politician. Return every comment number once."""

TOOL = {"name": "label_post", "input_schema": {
    "type": "object", "additionalProperties": False, "required": ["relevant", "presentation", "reason", "comments"],
    "properties": {
        "relevant": {"type": "boolean"},
        "presentation": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "reason": {"type": "string"},
        "comments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["n", "stance", "generic"],
            "properties": {"n": {"type": "integer"}, "stance": {"type": "string", "enum": ["for", "against", "neutral"]},
                           "generic": {"type": "boolean"}}}}}}}

# Posts of one politician that have segments but no label under this labeller version.
PENDING_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (oriane_id) oriane_id, raw_id, processor_version FROM post_processing
    ORDER BY oriane_id, processed_at DESC
), pp AS (
    SELECT DISTINCT oriane_id FROM run_post WHERE politician_id = %(pid)s AND relevance <> 'low'
)
SELECT p.oriane_id, l.raw_id, l.processor_version, p.platform, p.format, p.profile_handle, p.caption, p.published_at,
       EXISTS (SELECT 1 FROM post_person_role r WHERE r.oriane_id = p.oriane_id AND r.person_id = %(pid)s
               AND r.role = 'account_owner' AND r.status = 'verified') AS own_account
FROM pp JOIN post p ON p.oriane_id = pp.oriane_id JOIN latest l ON l.oriane_id = pp.oriane_id
{where}
ORDER BY p.published_at DESC, p.oriane_id
"""
NOT_LABELLED = ("WHERE NOT EXISTS (SELECT 1 FROM post_presentation x WHERE x.oriane_id = p.oriane_id "
                "AND x.politician_id = %(pid)s AND x.labeller_version = %(version)s)")
SEGMENTS_SQL = ("SELECT segment_id, oriane_id, raw_id, processor_version, source, ordinal, text FROM segment "
                "WHERE oriane_id = ANY(%s) ORDER BY oriane_id, ordinal")
REPEATED_SQL = ("SELECT lower(btrim(text)) AS t FROM segment WHERE source = 'comment' GROUP BY 1 "
                "HAVING count(DISTINCT oriane_id) >= %s AND length(lower(btrim(text))) >= %s")


def labeller_version(model: str) -> str:
    return f"{PROMPT_VERSION}:{model}"


def _clip(parts: list[str], limit: int) -> str:
    out, last = [], None
    for part in parts:
        part = " ".join((part or "").split())
        if part and part != last:      # on-screen text repeats frame after frame
            out.append(part)
            last = part
    return " ".join(out)[:limit]


def build_prompt(name: str, post: dict, segments: list[dict]) -> tuple[str, list[dict]]:
    """Prompt text and the comment segments it numbers, in order."""
    by = {s: [x for x in segments if x["source"] == s] for s in ("speech", "onscreen", "comment")}
    comments = [c for c in by["comment"] if (c.get("text") or "").strip()][:MAX_COMMENTS]
    lines = [f"Politician: {name}",
             f"Post: {post.get('platform')} {post.get('format') or 'post'} by @{post.get('profile_handle') or 'unknown'}"
             f"{' (the politician own account)' if post.get('own_account') else ''}, "
             f"published {str(post.get('published_at'))[:10]}",
             f"Caption: {(post.get('caption') or '').strip()[:CAPTION_CHARS] or '(none)'}",
             f"Transcript: {_clip([x['text'] for x in by['speech']], SPEECH_CHARS) or '(none)'}",
             f"On-screen text: {_clip([x['text'] for x in by['onscreen']], ONSCREEN_CHARS) or '(none)'}",
             "Comments:" if comments else "Comments: (none)"]
    lines += [f"{i}. {' '.join(c['text'].split())[:COMMENT_CHARS]}" for i, c in enumerate(comments, 1)]
    return "\n".join(lines), comments


def parse(output: dict, comments: list[dict], repeated: set[str]) -> tuple[dict, list[dict]]:
    """Validated post label and one stance row per comment; a comment the model skipped is neutral."""
    presentation = output.get("presentation")
    if presentation not in ("positive", "neutral", "negative") or not isinstance(output.get("relevant"), bool):
        raise ValueError("label is missing presentation or relevant")
    relevant = output["relevant"]
    label = {"relevant": relevant, "presentation": presentation if relevant else "neutral",
             "reason": " ".join(str(output.get("reason") or "").split())[:200] or None}
    given = {c.get("n"): c for c in output.get("comments") or [] if isinstance(c, dict)}
    rows = []
    for i, c in enumerate(comments, 1):
        got = given.get(i) or {}
        stance = got.get("stance") if got.get("stance") in ("for", "against", "neutral") else "neutral"
        text = c["text"].strip().lower()
        kind = "bot" if text in repeated else "unsure" if got.get("generic") is True else "human"
        rows.append({"segment_id": c["segment_id"], "stance": stance, "author_kind": kind})
    return label, rows


class Labeller:
    def __init__(self, conn, llm, clock=lambda: datetime.now(timezone.utc)):
        self.conn, self.llm, self.clock = conn, llm, clock
        self.version = labeller_version(llm.model)
        self._repeated: set[str] | None = None

    def _has_tables(self) -> bool:
        return self.conn.execute("SELECT to_regclass('post_presentation') AS t").fetchone()["t"] is not None

    def pending(self, politician_id: str) -> list[dict]:
        where = NOT_LABELLED if self._has_tables() else ""     # a read-only dry run may precede the migration
        return [dict(r) for r in self.conn.execute(PENDING_SQL.format(where=where),
                                                   {"pid": politician_id, "version": self.version}).fetchall()]

    def segments(self, posts: list[dict]) -> dict[str, list[dict]]:
        """Each post's segments from its latest processing only."""
        wanted = {p["oriane_id"]: (p["raw_id"], p["processor_version"]) for p in posts}
        out: dict[str, list[dict]] = {oid: [] for oid in wanted}
        ids = list(wanted)
        for i in range(0, len(ids), 200):
            for s in self.conn.execute(SEGMENTS_SQL, (ids[i:i + 200],)).fetchall():
                if (s["raw_id"], s["processor_version"]) == wanted[s["oriane_id"]]:
                    out[s["oriane_id"]].append(dict(s))
        return out

    def repeated(self) -> set[str]:
        if self._repeated is None:
            self._repeated = {r["t"] for r in self.conn.execute(REPEATED_SQL, (BOT_MIN_POSTS, BOT_MIN_CHARS)).fetchall()}
        return self._repeated

    def estimate(self, politician_id: str, name: str, max_posts: int | None = None) -> dict:
        """Calls and tokens a run would use, from the stored text; no model call."""
        posts = self.pending(politician_id)
        todo = posts[:max_posts] if max_posts else posts
        segs = self.segments(todo)
        tokens_in = tokens_out = comments = 0
        for p in todo:
            prompt, numbered = build_prompt(name, p, segs[p["oriane_id"]])
            tokens_in += SYSTEM_TOKENS + int(len(prompt) / CHARS_PER_TOKEN)
            tokens_out += OUTPUT_BASE_TOKENS + OUTPUT_TOKENS_PER_COMMENT * len(numbered)
            comments += len(numbered)
        return {"politician_id": politician_id, "labeller_version": self.version, "pending": len(posts),
                "would_label": len(todo), "comments": comments, "input_tokens": tokens_in, "output_tokens": tokens_out}

    def _label(self, name: str, post: dict, segments: list[dict]) -> dict:
        prompt, numbered = build_prompt(name, post, segments)
        try:
            output, usage = self.llm.extract(SYSTEM_PROMPT, prompt, TOOL)
            label, rows = parse(output, numbered, self.repeated())
        except Exception as exc:  # one bad answer must not stop the run; the post stays pending
            return {"post": post, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return {"post": post, "label": label, "comments": rows, "usage": usage or {}}

    def run(self, politician_id: str, name: str, max_posts: int | None = None, workers: int = 4) -> dict:
        posts = self.pending(politician_id)
        todo = posts[:max_posts] if max_posts else posts
        segs = self.segments(todo)
        self.repeated()  # load once, before the worker threads
        out = {"politician_id": politician_id, "labeller_version": self.version, "pending": len(posts), "labelled": 0,
               "failed": 0, "not_relevant": 0, "comments": 0, "input_tokens": 0, "output_tokens": 0, "errors": []}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = pool.map(lambda p: self._label(name, p, segs[p["oriane_id"]]), todo)
            for res in results:     # only this thread writes
                if "error" in res:
                    out["failed"] += 1
                    out["errors"] = (out["errors"] + [res["error"]])[:5]
                    continue
                self._store(politician_id, res)
                out["labelled"] += 1
                out["not_relevant"] += not res["label"]["relevant"]
                out["comments"] += len(res["comments"])
                out["input_tokens"] += res["usage"].get("input_tokens") or 0
                out["output_tokens"] += res["usage"].get("output_tokens") or 0
        return out

    def _store(self, politician_id: str, res: dict) -> None:
        now, oid, label, usage = self.clock(), res["post"]["oriane_id"], res["label"], res["usage"]
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO post_presentation(oriane_id, politician_id, labeller_version, model, relevant, "
                "presentation, reason, input_tokens, output_tokens, labelled_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (oriane_id, politician_id, labeller_version) DO UPDATE SET relevant = EXCLUDED.relevant, "
                "presentation = EXCLUDED.presentation, reason = EXCLUDED.reason, labelled_at = EXCLUDED.labelled_at",
                (oid, politician_id, self.version, self.llm.model, label["relevant"], label["presentation"],
                 label["reason"], usage.get("input_tokens"), usage.get("output_tokens"), now))
            for c in res["comments"]:
                self.conn.execute(
                    "INSERT INTO comment_stance(segment_id, oriane_id, politician_id, labeller_version, stance, "
                    "author_kind, labelled_at) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (segment_id, politician_id, labeller_version) DO UPDATE SET stance = EXCLUDED.stance, "
                    "author_kind = EXCLUDED.author_kind, labelled_at = EXCLUDED.labelled_at",
                    (c["segment_id"], oid, politician_id, self.version, c["stance"], c["author_kind"], now))


def tracked_politicians(conn, country: str) -> list[dict]:
    """Every politician with captured posts, with the name System B holds for them."""
    return [dict(r) for r in conn.execute(
        "SELECT rp.politician_id, max(ref.name) AS name FROM run_post rp "
        "LEFT JOIN politician_ref ref ON ref.politician_id = rp.politician_id "
        "WHERE rp.politician_id LIKE %s AND rp.relevance <> 'low' GROUP BY 1 ORDER BY 1",
        (f"person:{country.upper()}:%",)).fetchall()]
