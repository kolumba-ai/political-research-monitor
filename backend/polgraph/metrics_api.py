"""Read-only routes over the social metrics export (`python -m socialclaims metrics-export` / `metrics-import`).

Engagement (views, likes, comments, shares) comes from System B's captured posts; the presentation and feedback
indices come from imported labels. Both are joined to System A politician ids, so every figure links to a politician.
Nothing here calls Postgres, Oriane or past.dev.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException

from socialclaims import metrics as m

MAX_LIMIT = 200
SORTS = ("views", "likes", "comments", "shares", "published_at")
METHOD = {
    "presentation": "How each video presents the politician: +1 positive, 0 neutral, -1 negative, averaged over the "
                    "politician's videos and shown from -100 to +100.",
    "feedback": "Stance of human commenters toward the politician: (for - against) / human comments, from -100 to "
                f"+100. Likely bots are excluded and counted apart. A single video needs {m.MIN_HUMAN} human comments "
                "for its own label.",
    "threshold": m.THRESHOLD, "min_human_comments": m.MIN_HUMAN,
    "limits": "Labels were assigned by AI models and can be wrong. Only the most-liked comments of each video "
              "(at most 15) were analysed, not the full comment section.",
}


def engagement_summary(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    total = {k: sum(r.get(k) or 0 for r in rows) for k in ("views", "likes", "comments", "shares")}
    viewed = [r for r in rows if r.get("views")]
    interactions = sum((r.get("likes") or 0) + (r.get("comments") or 0) + (r.get("shares") or 0) for r in viewed)
    views = sum(r["views"] for r in viewed)
    platforms: dict[str, int] = {}
    for r in rows:
        platforms[r.get("platform") or "unknown"] = platforms.get(r.get("platform") or "unknown", 0) + 1
    dates = [r["published_at"] for r in rows if r.get("published_at")]
    return {**total, "posts": len(rows), "videos": sum(1 for r in rows if r.get("format") == "video"),
            "posts_with_views": len(viewed), "own_posts": sum(1 for r in rows if r.get("own_account")),
            "posts_with_claims": sum(1 for r in rows if r.get("claims")),
            "engagement_rate": round(interactions * 100 / views, 2) if views else None,
            "platforms": platforms, "first": min(dates, default=None), "last": max(dates, default=None)}


def _annotate(row: dict) -> dict:
    """Perception fields of one video in API shape."""
    return {"presentation": row.get("presentation"), "presentation_reason": row.get("presentation_reason"),
            "feedback": m.feedback_counts(row.get("feedback")),
            "reading": m.reading(row.get("presentation"), bool(row.get("own_account")))}


class MetricsExport:
    """Loads one country's metrics files and reloads them when they change on disk."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, tuple[tuple, dict]] = {}

    def load(self, country: str) -> dict | None:
        d = self.root / country.upper()
        files = [d / m.MANIFEST_FILE, d / m.POSTS_FILE, d / m.PERCEPTION_FILE]
        if not files[0].exists():
            return None
        stamp = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files)
        hit = self._cache.get(country)
        if hit and hit[0] == stamp:
            return hit[1]
        posts, perception = m.read_jsonl(files[1]), m.read_jsonl(files[2])
        people: dict[str, dict] = {}

        def person(key: str, pid: str | None, name: str | None) -> dict:
            p = people.setdefault(key, {"key": key, "politician_id": pid, "name": name, "posts": [], "perception": []})
            p["name"] = p["name"] or name
            return p
        for r in posts:
            person(r["politician_id"], r["politician_id"], r.get("politician_name"))["posts"].append(r)
        for r in perception:
            key = r.get("politician_id") or f"monitor:{r['monitor_slug']}"
            person(key, r.get("politician_id"), r.get("politician_name"))["perception"].append(r)
        data = {"manifest": json.loads(files[0].read_text(encoding="utf-8")), "people": people,
                "by_post": {(r["politician_id"], r["oriane_id"]): r for r in posts},
                "by_video": {(r.get("politician_id"), r["platform"], r["platform_id"]): r
                             for r in perception if r.get("politician_id")}}
        self._cache[country] = (stamp, data)
        return data

    def for_post(self, country: str, politician_id: str | None, oriane_id: str | None, platform: str | None,
                 platform_id: str | None) -> dict | None:
        """Engagement and perception of one politician's post, for the claim list; None when nothing is known."""
        data = self.load(country)
        if not data or not politician_id:
            return None
        post = data["by_post"].get((politician_id, oriane_id)) if oriane_id else None
        platform, platform_id = platform or (post or {}).get("platform"), platform_id or (post or {}).get("platform_id")
        video = data["by_video"].get((politician_id, platform, platform_id)) if platform_id else None
        if not post and not video:
            return None
        src = post or video
        out = {"engagement": {k: src.get(k) for k in ("views", "likes", "comments", "shares") if src.get(k) is not None}}
        if post and post.get("captured_at"):
            out["engagement"]["captured_at"] = post["captured_at"]
        if video:
            out["perception"] = _annotate(video)
        return out


def _videos(p: dict) -> list[dict]:
    """A politician's posts and labelled videos merged into one list, keyed by platform post id."""
    out: dict[tuple, dict] = {}
    for r in p["posts"]:
        key = (r.get("platform"), r.get("platform_id") or r["oriane_id"])
        out[key] = {**{k: r.get(k) for k in ("platform", "platform_id", "format", "url", "uploader_handle", "caption",
                                             "published_at", "views", "likes", "comments", "shares", "claims")},
                    "own_account": bool(r.get("own_account")), "captured": True, "labelled": False,
                    "presentation": None, "presentation_reason": None, "feedback": None, "reading": None}
    for r in p["perception"]:
        key = (r["platform"], r["platform_id"])
        base = out.get(key) or {**{k: r.get(k) for k in ("platform", "platform_id", "url", "uploader_handle", "caption",
                                                         "published_at", "views", "likes", "comments", "shares")},
                                "format": "video", "claims": 0, "captured": False}
        own = bool(base.get("own_account") or r.get("own_account"))
        out[key] = {**base, "own_account": own, "labelled": True, **_annotate({**r, "own_account": own})}
    return list(out.values())


def build_router(get_dir: Callable[[], Path], get_person: Callable[[str], dict | None] | None = None,
                 exports: dict | None = None) -> APIRouter:
    r = APIRouter()
    exports = {} if exports is None else exports

    def export(country: str) -> dict | None:
        root = Path(get_dir())
        return exports.setdefault(root, MetricsExport(root)).load(country.upper())

    def node(pid: str | None) -> dict:
        try:
            return (get_person(pid) if get_person and pid else None) or {}
        except Exception:  # the graph store is optional for these routes
            return {}

    def card(p: dict) -> dict:
        n = node(p["politician_id"])
        return {"key": p["key"], "politician_id": p["politician_id"], "name": n.get("name") or p["name"],
                "image_url": n.get("image_url"), "in_graph": bool(n),
                "engagement": engagement_summary(p["posts"]),
                "presentation": m.presentation_summary(p["perception"]), "feedback": m.feedback_summary(p["perception"])}

    def sources(data: dict) -> dict:
        man = data["manifest"]
        perception = {k: v for k, v in (man.get("perception") or {}).items() if k != "politicians"}
        return {"engagement": man.get("engagement"), "perception": perception or None}

    @r.get("/api/metrics/{code}/summary")
    def summary(code: str):
        data = export(code)
        if not data:
            return {"country": code.upper(), "available": False, "politicians": [], "sources": {}, "method": METHOD}
        roles = {p["slug"]: p for p in (data["manifest"].get("perception") or {}).get("politicians", [])}
        cards = []
        for p in data["people"].values():
            c = card(p)
            slug = p["perception"][0]["monitor_slug"] if p["perception"] else None
            c["role"] = (roles.get(slug) or {}).get("role")
            cards.append(c)
        cards.sort(key=lambda c: -((c["engagement"] or {}).get("views") or 0))
        every_post = [x for p in data["people"].values() for x in p["posts"]]
        every_pair = [x for p in data["people"].values() for x in p["perception"]]
        return {"country": code.upper(), "available": True, "politicians": cards, "sources": sources(data),
                "method": METHOD,
                "totals": {"engagement": engagement_summary(list({x["oriane_id"]: x for x in every_post}.values())),
                           "presentation": m.presentation_summary(every_pair),
                           "feedback": m.feedback_summary(every_pair)}}

    @r.get("/api/metrics/{code}/politicians/{key:path}")
    def politician(code: str, key: str, sort: str = "views", labelled: bool | None = None,
                   presentation: str | None = None, platform: str | None = None, limit: int = 25, offset: int = 0):
        if limit < 1 or limit > MAX_LIMIT or offset < 0 or sort not in SORTS:
            raise HTTPException(422, f"limit must be 1..{MAX_LIMIT}, offset >= 0 and sort one of {', '.join(SORTS)}")
        data = export(code)
        p = (data or {}).get("people", {}).get(key)
        base = {"country": code.upper(), "key": key, "method": METHOD, "sources": sources(data) if data else {}}
        if not p:
            return {**base, "available": False, "politician_id": key if key.startswith("person:") else None,
                    "engagement": None, "presentation": None, "feedback": None, "total": 0, "videos": []}
        videos = [v for v in _videos(p)
                  if (labelled is None or v["labelled"] == labelled)
                  and (presentation is None or v["presentation"] == presentation)
                  and (platform is None or v["platform"] == platform)]
        if sort == "published_at":
            videos.sort(key=lambda v: v.get("published_at") or "", reverse=True)
        else:
            videos.sort(key=lambda v: v.get(sort) or -1, reverse=True)
        return {**base, **card(p), "available": True, "total": len(videos), "offset": offset, "limit": limit,
                "videos": videos[offset:offset + limit]}

    return r
