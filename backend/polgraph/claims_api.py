"""Read-only routes over System B's social-claims export (`python -m socialclaims pull-pastdev`).

The export is `<CLAIMS_EXPORT_DIR>/<ISO2>/{manifest.json,posts.jsonl,claims.jsonl}`, plus `documents.jsonl` and
`evidence.jsonl` (System C evidence) and `storylines.json` (`python -m socialclaims storylines` / `topics`) when present; nothing
here calls past.dev. Claims are context about what was said on social media, not verified facts about the politician.
An evidence stance is one document's relation to a claim, never a verdict on it.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException

from socialclaims import storylines as st

MAX_LIMIT = 500


def _counts(rows: list[dict], key: str) -> dict[str, int]:
    return dict(Counter(str(r.get(key)) for r in rows if r.get(key) is not None).most_common())


def _by_day(rows: list[dict]) -> list[dict]:
    days = Counter((r.get("published_at") or "")[:10] for r in rows if r.get("published_at"))
    return [{"date": d, "claims": n} for d, n in sorted(days.items())]


def stats(claims: list[dict]) -> dict:
    return {"claims": len(claims), "posts": len({c.get("data_point_id") for c in claims}),
            "by_type": _counts(claims, "type"), "by_speaker": _counts(claims, "speaker"),
            "by_platform": _counts(claims, "platform"), "by_day": _by_day(claims),
            "first": min((c["published_at"] for c in claims if c.get("published_at")), default=None),
            "last": max((c["published_at"] for c in claims if c.get("published_at")), default=None)}


class ClaimsExport:
    """Loads one country's export and reloads it when the files change on disk."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, tuple[tuple, dict]] = {}

    def load(self, country: str) -> dict | None:
        d = self.root / country.upper()
        files = [d / "manifest.json", d / "claims.jsonl", d / "posts.jsonl"]
        if not all(f.exists() for f in files):
            return None
        optional = [d / "documents.jsonl", d / "evidence.jsonl", d / "storylines.json"]
        stamp = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files + optional)
        hit = self._cache.get(country)
        if hit and hit[0] == stamp:
            return hit[1]

        def jsonl(p: Path) -> list[dict]:
            # split on "\n" only: captions may hold U+2028 and similar, which str.splitlines() treats as line ends
            return [json.loads(line) for line in p.read_text(encoding="utf-8").split("\n") if line.strip()]
        posts = {p["data_point_id"]: p for p in jsonl(files[2])}
        claims = jsonl(files[1])
        for c in claims:  # the post is the authority for the link and account; older exports left them off the claim
            post = posts.get(c.get("data_point_id")) or {}
            for k in ("url", "uploader_handle", "platform_id", "oriane_content_id"):
                c[k] = c.get(k) or post.get(k)
        by_pol: dict[str, list[dict]] = {}
        for c in claims:
            by_pol.setdefault(c.get("politician_id") or "", []).append(c)
        stories = json.loads(optional[2].read_text(encoding="utf-8")) if optional[2].exists() else {}
        data = {"manifest": json.loads(files[0].read_text(encoding="utf-8")), "claims": claims, "posts": posts,
                "by_politician": by_pol, "has_evidence": optional[1].exists(),
                "evidence": st.evidence_index(st.read_jsonl(optional[1]), st.read_jsonl(optional[0])),
                "storylines": stories.get("politicians") or {}, "storylines_updated_at": stories.get("updated_at")}
        self._cache[country] = (stamp, data)
        return data


def build_router(get_dir: Callable[[], Path], get_person: Callable[[str], dict | None] | None = None,
                 get_metrics: Callable[[str, dict], dict | None] | None = None) -> APIRouter:
    """`get_metrics(country, claim)` adds the post's engagement and perception labels from the metrics export."""
    r = APIRouter()
    exports: dict[Path, ClaimsExport] = {}

    def export(country: str) -> dict | None:
        root = Path(get_dir())
        return exports.setdefault(root, ClaimsExport(root)).load(country.upper())

    @r.get("/api/claims/{code}/summary")
    def summary(code: str):
        data = export(code)
        if not data:
            return {"country": code.upper(), "available": False, "politicians": [], "stats": stats([])}
        m = data["manifest"]
        by_pol: dict[str, list[dict]] = {}
        for c in data["claims"]:
            by_pol.setdefault(c.get("politician_id") or "", []).append(c)
        def image(pid: str) -> str | None:
            try:
                return ((get_person(pid) if get_person and pid else None) or {}).get("image_url")
            except Exception:  # the graph store is optional for this route
                return None
        politicians = [{"politician_id": pid or None, "name": rows[0].get("politician_name"), "image_url": image(pid),
                        **stats(rows)} for pid, rows in by_pol.items()]
        politicians.sort(key=lambda p: -p["claims"])
        return {"country": code.upper(), "available": True, "source": m.get("source"),
                "pulled_at": m.get("pulled_at"), "partial_posts": m.get("partial_posts", 0),
                "politicians": politicians, "stats": stats(data["claims"])}

    @r.get("/api/claims/{code}/storylines/{politician_id:path}")
    def storylines(code: str, politician_id: str, storyline: str | None = None, week: str | None = None,
                   area: str | None = None, evidence: str | None = None, recurring: bool = False):
        """Without `storyline`: the week grid (topics and the storylines under them), optionally only the storylines
        of one subject `area`, with `evidence` of one stance ("none" for no evidence) or `recurring` in more than one
        week. With `storyline`: that storyline in full, every post, statement and evidence document; `week` (a Monday,
        YYYY-MM-DD) keeps one week of it."""
        data = export(code)
        rows = (data or {}).get("by_politician", {}).get(politician_id, [])
        pol = (data or {}).get("storylines", {}).get(politician_id)
        base = {"country": code.upper(), "politician_id": politician_id, "available": bool(data),
                "pulled_at": data["manifest"].get("pulled_at") if data else None,
                "has_evidence": bool(data and data["has_evidence"]),
                "grouped_at": data["storylines_updated_at"] if data and pol else None}
        if storyline is None:
            if evidence not in (None, "none", *st.STANCES):
                raise HTTPException(422, "evidence must be supports, contradicts, context or none")
            return {**base, **st.matrix(rows, pol, data["evidence"] if data else None, area, evidence, recurring)}
        found = st.detail(rows, data["posts"] if data else {}, pol, data["evidence"] if data else {}, storyline, week)
        if found is None:
            raise HTTPException(404, f"no storyline {storyline} for {politician_id}")
        return {**base, "storyline": found}

    @r.get("/api/claims/{code}/politicians/{politician_id:path}")
    def politician_claims(code: str, politician_id: str, type: str | None = None, speaker: str | None = None,
                          platform: str | None = None, about_politician: bool | None = None, q: str | None = None,
                          limit: int = 100, offset: int = 0):
        if limit < 1 or limit > MAX_LIMIT or offset < 0:
            raise HTTPException(422, f"limit must be 1..{MAX_LIMIT} and offset >= 0")
        data = export(code)
        rows = [c for c in (data["claims"] if data else []) if c.get("politician_id") == politician_id]
        all_stats = stats(rows)
        needle = (q or "").casefold()
        rows = [c for c in rows
                if (type is None or c.get("type") == type) and (speaker is None or c.get("speaker") == speaker)
                and (platform is None or c.get("platform") == platform)
                and (about_politician is None or c.get("about_politician") == about_politician)
                and (not needle or needle in f"{c.get('text') or ''} {c.get('text_en') or ''}".casefold())]
        page = []
        for c in rows[offset:offset + limit]:
            post = data["posts"].get(c.get("data_point_id")) or {}
            evidence = data["evidence"].get((c.get("proposition_id") or "", c.get("politician_id") or ""), [])
            extra = (get_metrics(code, c) if get_metrics else None) or {}
            page.append({**c, "engagement": post.get("engagement") or extra.get("engagement") or {},
                         "perception": extra.get("perception"), "format": post.get("format"),
                         "caption": post.get("caption"), "transcript_excerpt": post.get("transcript_excerpt"),
                         "evidence": evidence, "evidence_counts": st.stance_counts(evidence)})
        return {"country": code.upper(), "politician_id": politician_id, "available": bool(data),
                "pulled_at": data["manifest"].get("pulled_at") if data else None,
                "total": len(rows), "offset": offset, "limit": limit, "stats": all_stats, "claims": page}

    return r
