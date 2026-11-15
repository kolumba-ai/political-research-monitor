"""Composite Oriane query per politician (seven named branches) and its deterministic `query_id`."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from polgraph.ids import strip_accents

from .systema import PersonSnapshot

QUERY_FORMAT = 1
BRANCHES = ("own", "coauthored", "tagged", "spoken", "written", "onscreen", "hashtag")


@dataclass
class QueryTerms:
    """What System B adds on top of System A's identity: verified handles/profile ids, hashtags, ASR variants."""
    handles: dict[str, list[str]] = field(default_factory=dict)      # platform -> verified handles
    profile_ids: list[str] = field(default_factory=list)              # Oriane profileIds of verified accounts
    hashtags: list[str] = field(default_factory=list)
    extra_variants: list[str] = field(default_factory=list)           # ASR misspellings, distinctive surname, ...
    excluded_variants: list[str] = field(default_factory=list)


def _dedupe(values) -> list[str]:
    seen, out = set(), []
    for v in values:
        v = " ".join((v or "").split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


NAME_PARTICLES = {"de", "del", "la", "las", "los", "y"}


def name_variants(name: str, aliases: list[str] = (), extra: list[str] = (), excluded: list[str] = ()) -> list[str]:
    """Full name, accent-free form, and Spanish short forms (drop the maternal surname; given name + paternal).
    A bare surname is never generated automatically; add it as an explicit extra term only when distinctive."""
    out: list[str] = []
    for full in [name, *aliases]:
        toks = full.split()
        forms = [full]
        if len(toks) >= 3:
            # Spanish names: given name(s) + paternal + maternal surname. Particles ("de la Garza") belong to the
            # paternal surname and are never a given name ("María de Lourdes" yields "María", "Lourdes").
            i = len(toks) - 2
            while i > 1 and toks[i - 1].lower() in NAME_PARTICLES:
                i -= 1
            paternal = " ".join(toks[i:-1])
            forms += [" ".join(toks[:-1]),
                      *(f"{g} {paternal}" for g in toks[:i] if g.lower() not in NAME_PARTICLES)]
        for f in forms:
            out += [f, strip_accents(f)]
    out += list(extra)
    drop = {e.lower() for e in excluded}
    return [v for v in _dedupe(out) if v.lower() not in drop and len(v.split()) >= (1 if v in extra else 2)]


def _handles(terms: QueryTerms) -> list[str]:
    return _dedupe(h.lstrip("@").lower() for hs in terms.handles.values() for h in hs)


def _hashtags(terms: QueryTerms) -> list[str]:
    return _dedupe(("#" + h.lstrip("#")).lower() for h in terms.hashtags)


def build_query(snapshot: PersonSnapshot, terms: QueryTerms, platforms: list[str]) -> dict:
    """The stored query JSON. Date window and `id.excludes` are per-run and added by `request_body`, so the
    `query_id` stays stable for a person until the identity snapshot or the added terms change."""
    variants = name_variants(snapshot.name, snapshot.aliases, terms.extra_variants, terms.excluded_variants)
    by_platform = {p: _dedupe(h.lstrip("@").lower() for h in hs)
                   for p, hs in sorted(terms.handles.items()) if hs and p in platforms}
    branches: list[dict] = []
    # Handles are only unique per platform, so handle branches are split per platform ("tagged:tiktok") and
    # nesting stays within Oriane's two-level limit. Oriane profile ids are globally unique.
    if terms.profile_ids:
        branches.append({"name": "own", "operator": "or",
                         "filters": {"profileId": {"includes": sorted(set(terms.profile_ids))}}})
    for name, fld in (("own", "profileHandle"), ("coauthored", "coAuthorHandles"), ("tagged", "mentionHandles")):
        for p, hs in by_platform.items():
            branches.append({"name": f"{name}:{p}", "operator": "and",
                             "filters": {"platform": {"includes": [p]}, fld: {"exactMatch": {"values": hs}}}})
    for name, fld in (("spoken", "transcript"), ("written", "caption"), ("onscreen", "onScreenText")):
        if variants:
            branches.append({"name": name, "operator": "or",
                             "filters": {fld: {"includesExactly": {"values": variants, "operator": "or"}}}})
    tags = _hashtags(terms)
    if tags:
        branches.append({"name": "hashtag", "operator": "or",
                         "filters": {"hashtags": {"exactMatch": {"values": tags, "operator": "or"}}}})
    branches = [b for b in branches if b]
    return {
        "format": QUERY_FORMAT,
        "politician_id": snapshot.politician_id,
        "platforms": sorted(platforms),
        "branches": branches,
    }


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def query_id(query: dict) -> str:
    return hashlib.sha256(canonical_json(query).encode("utf-8")).hexdigest()


def count_filter_values(node) -> int:
    """Oriane's 500-value budget counts every value across all levels."""
    if isinstance(node, list):
        return sum(count_filter_values(v) if isinstance(v, (dict, list)) else 1 for v in node)
    if isinstance(node, dict):
        return sum(count_filter_values(v) if isinstance(v, (dict, list)) else (1 if k in ("after", "before") else 0)
                   for k, v in node.items() if k not in ("operator", "name"))
    return 0


def request_body(query: dict, date_from: str, date_to: str, exclude_ids: list[str] = (),
                 max_filter_values: int = 500) -> tuple[dict, int]:
    """Oriane request: top-level `and` of shared constraints with an `or` of the named branches.
    Returns the body and how many `id.excludes` values fit in the 500-value budget."""
    if not query["branches"]:
        raise ValueError("query has no branches (no name variants, handles or hashtags)")
    shared: dict = {"platform": {"includes": query["platforms"]},
                    "publishedAt": {"after": date_from, "before": date_to}}
    body = {"operator": "and", "filters": shared,
            "queries": [{"operator": "or", "queries": query["branches"]}]}
    used = count_filter_values(body)
    if used > max_filter_values:
        raise ValueError(f"query uses {used} filter values; Oriane allows {max_filter_values}")
    room = max(0, max_filter_values - used)
    excl = list(exclude_ids)[:room]
    if excl:
        shared["id"] = {"excludes": excl}
    return body, len(excl)
