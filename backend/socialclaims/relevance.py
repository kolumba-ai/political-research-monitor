"""Branch-weighted post-to-person relevance. A match never establishes authorship or who is speaking."""
from __future__ import annotations

from polgraph.ids import normalize_text

# Probability-like weight of each matched branch; combined as 1 - prod(1 - w).
BRANCH_WEIGHTS = {
    "own": 0.9,          # verified handle / profileId only (handles are only queried once verified)
    "coauthored": 0.75,
    "tagged": 0.55,      # mention, not authorship
    "written": 0.55,
    "onscreen": 0.45,
    "hashtag": 0.4,
    "spoken": 0.35,      # transcript-only matches stay low confidence (ASR is weak on proper nouns)
}


def _texts(post: dict) -> str:
    parts = [post.get("caption") or "", post.get("title") or ""]
    for m in post.get("media") or []:
        parts += [m.get("caption") or "", m.get("transcript") or "", m.get("onScreenText") or ""]
    return normalize_text(" ".join(parts))


def score(post: dict, matched: list[str], ctx: dict) -> tuple[float, dict]:
    """`ctx`: `full_names` (normalized full-name variants), `entities` (party/coalition/office terms),
    `languages`, `country_code`."""
    basis: dict = {"branches": sorted(matched)}
    miss = 1.0
    for b in matched:
        miss *= 1 - BRANCH_WEIGHTS.get(b, 0.2)
    s = 1 - miss

    text = f" {_texts(post)} "
    full_hit = any(f" {n} " in text for n in ctx.get("full_names", []) if n)
    if full_hit:
        s += 0.1
    basis["full_name_hit"] = full_hit

    ents = [e for e in ctx.get("entities", []) if e and f" {e} " in text]
    if ents:
        s += min(0.1, 0.05 * len(ents))
    basis["entities"] = ents[:5]

    langs = {post.get("captionLanguage")} | {m.get("transcriptLanguage") for m in post.get("media") or []}
    lang_hit = bool(langs & set(ctx.get("languages", [])))
    if lang_hit:
        s += 0.05
    basis["language_hit"] = lang_hit

    cc, conf = post.get("locationCountryCode"), post.get("locationCountryConfidence")
    if cc:
        weight = 0.05 if conf is None else 0.05 * float(conf)
        s += weight if cc.upper() == ctx.get("country_code", "").upper() else -2 * weight
        basis["location"] = {"code": cc, "confidence": conf}

    return round(max(0.0, min(1.0, s)), 4), basis


def bucket(value: float, keep: float, review: float) -> str:
    return "keep" if value >= keep else "review" if value >= review else "low"
