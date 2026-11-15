"""Phase 2: segments and speaker roles (no LLM, no provider calls).

Each post's saved raw snapshot becomes ordered, time-coded segments (speech, on-screen, caption, comment), and each
tracked politician gets separate roles on it. A politician is only ever `speaker` with status `probable`, from their own
verified account carrying speech, and that case goes to review; Oriane has no diarization, so nothing here can
verify who actually speaks. Mentions, handles and name banners never make someone the speaker.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

from polgraph.ids import normalize_text

from .capture import _ts, utcnow
from .identity import load_terms, merged_ids, relevance_context
from .relevance import _flat
from .systema import GraphReader, read_person

PROCESSOR_VERSION = "segments-v1"
QUOTE_VERBS = r"(dijo|afirmo|aseguro|declaro|senalo|advirtio|anuncio|prometio|acuso|nego|reconocio|explico)"


def load_post(raw_dir: Path | str, payload_path: str, cache: dict | None = None) -> dict:
    file, pointer = payload_path.split("#")
    cache = {} if cache is None else cache
    if file not in cache:
        cache[file] = json.loads((Path(raw_dir) / file).read_text())
    post = cache[file]
    for key in pointer.strip("/").split("/"):
        post = post[int(key)] if isinstance(post, list) else post[key]
    return post


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _str(v) -> str | None:
    return None if v in (None, "", "None") else str(v)


def build_segments(post: dict) -> list[dict]:
    """Ordered segments: speech chunks by time, on-screen text merged across consecutive frames, caption, comments."""
    segs: list[dict] = []
    for m in post.get("media") or []:
        lang = _str(m.get("transcriptLanguage"))
        chunks = m.get("transcriptChunks") or []
        for c in chunks:
            if _str(c.get("text")):
                segs.append({"source": "speech", "text": c["text"].strip(), "language": lang,
                             "start_s": _num(c.get("startSeconds")), "end_s": _num(c.get("endSeconds"))})
        if not chunks and _str(m.get("transcript")):
            segs.append({"source": "speech", "text": m["transcript"].strip(), "language": lang,
                         "start_s": None, "end_s": None})
        runs: dict[str, dict] = {}
        frames = sorted(m.get("frames") or [], key=lambda f: _num(f.get("timestampSeconds")) or 0)
        for f in frames:
            t = _num(f.get("timestampSeconds"))
            texts = {(r.get("text") or "").strip() for r in f.get("onScreenTextRegions") or []} - {""}
            for gone in set(runs) - texts:
                segs.append(runs.pop(gone))
            for txt in texts:
                if txt in runs:
                    runs[txt]["end_s"] = t
                else:
                    runs[txt] = {"source": "onscreen", "text": txt, "language": None, "start_s": t, "end_s": t}
        segs.extend(runs.values())
        if not frames:
            for txt in _flat(m.get("onScreenText")):
                segs.append({"source": "onscreen", "text": txt.strip(), "language": None, "start_s": None,
                             "end_s": None})
    caption = " ".join(x for x in [_str(post.get("caption")), " ".join(post.get("hashtags") or [])] +
                       [_str(m.get("caption")) for m in post.get("media") or []] if x)
    if caption.strip():
        segs.append({"source": "caption", "text": caption.strip(), "language": _str(post.get("captionLanguage")),
                     "start_s": None, "end_s": None})
    for c in post.get("popularComments") or []:
        if _str(c.get("content")):
            segs.append({"source": "comment", "text": c["content"], "language": None, "start_s": None,
                         "end_s": None, "commented_at": _ts(c.get("publishedAt"))})
    order = {"speech": 0, "onscreen": 1, "caption": 2, "comment": 3}
    segs.sort(key=lambda s: (order[s["source"]], s["start_s"] if s["start_s"] is not None else -1))
    return [s for s in segs if s["text"]]


def assign_roles(post: dict, segs: list[dict], person_id: str, names: list[str], accounts: list[dict]) -> list[dict]:
    """Roles for one politician on one post, each with its evidence. `accounts`: verified politician_account rows."""
    roles: list[dict] = []
    platform = post.get("platform")
    own = [a for a in accounts if a["platform"] == platform and (
        (a["oriane_profile_id"] and a["oriane_profile_id"] == post.get("profileId"))
        or a["handle"].lower() == (post.get("profileHandle") or "").lower())]
    if own:
        roles.append({"role": "account_owner", "status": "verified",
                      "evidence": [{"verified_account": a["account_id"], "handle": a["handle"]} for a in own]})
    co = {(c.get("handle") or c.get("profileHandle") or str(c)).lower().lstrip("@") if isinstance(c, dict)
          else str(c).lower().lstrip("@") for c in post.get("coAuthors") or []}
    co_hits = [a for a in accounts if a["platform"] == platform and a["handle"].lower() in co]
    if co_hits:
        roles.append({"role": "coauthor", "status": "verified",
                      "evidence": [{"verified_account": a["account_id"], "handle": a["handle"]} for a in co_hits]})
    mentioned, quoted = [], []
    tags = {str(m).lower().lstrip("@") for m in post.get("mentions") or []}
    for a in accounts:
        if a["platform"] == platform and a["handle"].lower() in tags:
            mentioned.append({"tag": a["handle"]})
    for i, s in enumerate(segs):
        if s["source"] == "comment":
            continue
        text = f" {normalize_text(s['text'])} "
        for n in names:
            if f" {n} " in text:
                mentioned.append({"segment": i, "source": s["source"], "name": n})
                if s["source"] != "speech" and re.search(
                        rf"(\b{re.escape(n)}\b.{{0,40}}\b{QUOTE_VERBS}\b|\b{QUOTE_VERBS}\b.{{0,15}}\b{re.escape(n)}\b"
                        rf"|\bsegun\s+{re.escape(n)}\b)", text):
                    quoted.append({"segment": i, "source": s["source"], "name": n})
                break
    if mentioned:
        roles.append({"role": "mentioned", "status": "verified", "evidence": mentioned})
    if quoted:
        roles.append({"role": "quoted", "status": "probable", "evidence": quoted})
    if own and any(s["source"] == "speech" for s in segs):
        roles.append({"role": "speaker", "status": "probable",
                      "evidence": [{"own_account_with_speech": a["account_id"]} for a in own]})
    else:
        roles.append({"role": "speaker", "status": "unknown", "evidence": []})
    return [{**r, "person_id": person_id} for r in roles]


def _segment_id(raw_id: str, ordinal: int, seg: dict) -> str:
    return hashlib.sha256(f"{PROCESSOR_VERSION}|{raw_id}|{ordinal}|{seg['source']}|{seg['text']}".encode()).hexdigest()[:24]


def process_politician(conn, graph: GraphReader, raw_dir: Path | str, politician_id: str,
                       now: datetime | None = None, review_views: int = 100_000) -> dict:
    """Segments + roles for every non-`low` post of the politician, from each post's latest saved snapshot.
    Idempotent per (post, raw snapshot, processor version)."""
    now = now or utcnow()
    snap = read_person(graph, politician_id)
    if snap is None:
        raise LookupError(f"{politician_id} not in System A")
    names = relevance_context(snap, load_terms(conn, politician_id))["full_names"]
    ids = merged_ids(conn, politician_id)
    accounts = conn.execute("SELECT account_id, platform, handle, oriane_profile_id FROM politician_account "
                            "WHERE politician_id = ANY(%s) AND status = 'verified'", (ids,)).fetchall()
    rows = conn.execute(
        "SELECT DISTINCT p.oriane_id, p.latest_raw_id AS raw_id, d.payload_path, p.published_at FROM run_post rp "
        "JOIN post p ON p.oriane_id = rp.oriane_id JOIN raw_data d ON d.raw_id = p.latest_raw_id "
        "WHERE rp.politician_id = ANY(%s) AND rp.relevance <> 'low' ORDER BY p.published_at, p.oriane_id",
        (ids,)).fetchall()
    out = {"politician_id": politician_id, "posts": len(rows), "processed": 0, "skipped": 0, "unverifiable": 0,
           "segments": 0, "roles": {}, "review": 0}
    cache: dict = {}
    for r in rows:
        if not r["payload_path"]:
            out["unverifiable"] += 1
            continue
        post = load_post(raw_dir, r["payload_path"], cache)
        done = conn.execute("SELECT 1 FROM post_processing WHERE oriane_id = %s AND raw_id = %s AND "
                            "processor_version = %s", (r["oriane_id"], r["raw_id"], PROCESSOR_VERSION)).fetchone()
        segs = build_segments(post)
        roles = assign_roles(post, segs, politician_id, names, accounts)
        with conn.transaction():
            if not done:
                _store_post(conn, r, post, segs, now)
                out["processed"] += 1
                out["segments"] += len(segs)
            else:
                out["skipped"] += 1
            for role in roles:
                conn.execute(
                    "INSERT INTO post_person_role(oriane_id, raw_id, processor_version, person_id, role, status, "
                    "evidence, assigned_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (r["oriane_id"], r["raw_id"], PROCESSOR_VERSION, politician_id, role["role"], role["status"],
                     Jsonb(role["evidence"]), now))
                out["roles"][f"{role['role']}:{role['status']}"] = out["roles"].get(f"{role['role']}:{role['status']}", 0) + 1
            speaker = next(x for x in roles if x["role"] == "speaker")
            views = _num(post.get("viewsCount")) or 0
            reason = ("own_account_speech_unverified" if speaker["status"] == "probable"
                      else "high_reach_unknown_speaker" if views >= review_views and
                      any(x["role"] in ("mentioned", "quoted") for x in roles) else None)
            if reason:
                cur = conn.execute(
                    "INSERT INTO review(item_type, item_id, reason, payload) VALUES ('speaker', %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (f"{r['oriane_id']}|{politician_id}", reason,
                     Jsonb({"raw_id": r["raw_id"], "url": post.get("url"), "views": views,
                            "processor_version": PROCESSOR_VERSION})))
                out["review"] += cur.rowcount
    return out


def _store_post(conn, r: dict, post: dict, segs: list[dict], now: datetime) -> None:
    audio = _str(post.get("audioPlatformId"))
    canonical = r["oriane_id"]
    has_speech = any(s["source"] == "speech" for s in segs)
    if audio and has_speech and post.get("audioType") == "original":
        hit = conn.execute(
            "SELECT pp.canonical_oriane_id FROM post_processing pp JOIN post p ON p.oriane_id = pp.canonical_oriane_id "
            "WHERE pp.audio_platform_id = %s AND pp.processor_version = %s ORDER BY p.published_at LIMIT 1",
            (audio, PROCESSOR_VERSION)).fetchone()
        if hit:
            canonical = hit["canonical_oriane_id"]
    counts: dict[str, int] = {}
    for i, s in enumerate(segs):
        counts[s["source"]] = counts.get(s["source"], 0) + 1
        conn.execute(
            "INSERT INTO segment(segment_id, oriane_id, raw_id, processor_version, source, ordinal, text, language, "
            "start_s, end_s, commented_at, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (_segment_id(r["raw_id"], i, s), r["oriane_id"], r["raw_id"], PROCESSOR_VERSION, s["source"], i, s["text"],
             s["language"], s["start_s"], s["end_s"], s.get("commented_at"), now))
    conn.execute(
        "INSERT INTO post_processing(oriane_id, raw_id, processor_version, uploader_profile_id, uploader_handle, "
        "audio_platform_id, canonical_oriane_id, low_information, segment_counts, processed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (r["oriane_id"], r["raw_id"], PROCESSOR_VERSION, post.get("profileId"), post.get("profileHandle"),
         audio if has_speech and post.get("audioType") == "original" else None, canonical,
         not any(s["source"] in ("speech", "onscreen") for s in segs), Jsonb(counts), now))
