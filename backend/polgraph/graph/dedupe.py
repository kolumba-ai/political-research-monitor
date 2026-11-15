"""Entity resolution for people, offices and parties."""
from __future__ import annotations

import re

from ..ids import name_tokens, name_variants, normalize_party_key, normalize_text


def same_person(a: str, b: str) -> bool:
    """Match if any prefix-stripped variant pair matches (see ids.name_variants)."""
    return any(_same_tokens(ta, tb) for ta in name_variants(a) for tb in name_variants(b))


def _same_tokens(ta: list[str], tb: list[str]) -> bool:
    """Conservative name match: same first given name and at least one shared surname (>=3 chars),
    or one token set contains the other with >=2 tokens. Handles accents, honorifics, particles
    and the Spanish double-surname convention ('Claudia Sheinbaum' == 'Claudia Sheinbaum Pardo')."""
    if len(ta) < 2 or len(tb) < 2:
        return False
    if ta == tb:
        return True
    sa, sb = set(ta), set(tb)
    if (sa <= sb or sb <= sa) and min(len(sa), len(sb)) >= 2 and ta[0] == tb[0]:
        return True
    if ta[0] != tb[0]:
        return False
    shared = {t for t in sa & sb if len(t) >= 3} - {ta[0]}
    # Require the shared surname to be the first surname of at least one of the two names,
    # so 'Juan Pérez López' and 'Juan García López' (shared maternal surname only) don't merge.
    return bool(shared) and (ta[1] in shared or tb[1] in shared)


def find_person(store, iso2: str, name: str) -> dict | None:
    toks = name_tokens(name)
    if len(toks) < 2:
        return None
    candidates: dict[str, dict] = {}
    for probe in {toks[1], toks[-1]}:
        for n in store.search(probe, ["Person"], limit=50):
            candidates[n["id"]] = n
    for n in candidates.values():
        if n.get("country") != iso2.upper():
            continue
        names = [n.get("name", "")] + list(n.get("aliases") or [])
        if any(same_person(name, x) for x in names):
            return n
    return None


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(normalize_text(a).split()), set(normalize_text(b).split())
    stop = {"of", "the", "de", "del", "la", "el"}
    ta, tb = ta - stop, tb - stop
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _qualifier(title: str) -> str:
    m = re.search(r"\(([^)]*)\)\s*$", title or "")
    return normalize_text(m.group(1)) if m else ""


def find_office(store, body_node_id: str, title_en: str, title_local: str | None, threshold: float = 0.75,
                seat_type: str | None = None) -> dict | None:
    """Same body + same local title, or similar English title. Offices whose bracketed qualifier
    ('(reserved seat for women)' vs '(reserved seat for minorities)') or seat_type differ never match."""
    for ch in store.children(body_node_id):
        n = ch["node"]
        if n["label"] != "PoliticalOffice":
            continue
        if _qualifier(title_en) != _qualifier(n.get("name", "")):
            continue
        if seat_type and n.get("seat_type") and n["seat_type"] != seat_type:
            continue
        if title_local and n.get("title_local") and normalize_text(title_local) == normalize_text(n["title_local"]):
            return n
        if title_similarity(title_en, n.get("name", "")) >= threshold:
            return n
    return None


def party_key(name: str | None, acronym: str | None) -> str | None:
    """Prefer the acronym (stable across languages/word order); fall back to the normalised name."""
    if acronym and 1 < len(normalize_text(acronym).replace(" ", "")) <= 12:
        return normalize_text(acronym).replace(" ", "")
    if name:
        key = normalize_party_key(name)
        return key.replace(" ", "") if len(key) <= 12 else key
    return None
