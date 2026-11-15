"""Entity resolution for people, offices and parties."""
from __future__ import annotations

import re

from ..ids import name_tokens, name_variants, normalize_party_key, normalize_text


def same_person(a: str, b: str) -> bool:
    """Match if any prefix-stripped variant pair matches (see ids.name_variants)."""
    return any(_same_tokens(ta, tb) for ta in name_variants(a) for tb in name_variants(b))


def _readings(t: list[str]) -> list[tuple[list[str], str, str | None]]:
    """Possible (given names, paternal surname, maternal surname|None) readings of a given-name-first name.
    2 words: given + surname. 4+ words: given… + paternal + maternal. 3 words are ambiguous:
    'Clara Brugada Molina' (given + 2 surnames) or 'Miguel Ángel Navarro' (2 given + surname)."""
    if len(t) == 2:
        return [([t[0]], t[1], None)]
    if len(t) == 3:
        return [([t[0]], t[1], t[2]), (t[:2], t[2], None)]
    return [(t[:-2], t[-2], t[-1])]


def _given_compatible(a: list[str], b: list[str]) -> bool:
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_[:len(short)] == short


def _same_tokens(ta: list[str], tb: list[str]) -> bool:
    """Name match on surname STRUCTURE, never on shared given names or crossed surnames.

    Two names match if some reading of each has the same paternal surname, maternal surnames that don't
    conflict, and given names where one is a prefix of the other. So 'Claudia Sheinbaum' == 'Claudia
    Sheinbaum Pardo' and 'Clara Brugada Molina' == 'Clara Marina Brugada Molina', but not
    'María Teresa Ealy Díaz' / 'María Teresa Jiménez Esquivel' (shared given names), 'Juan Pérez López' /
    'Juan García López' (shared maternal surname), or 'José Antonio Gali López' / 'José Antonio López Ruíz'
    (one's maternal surname is the other's paternal surname)."""
    if len(ta) < 2 or len(tb) < 2:
        return False
    if ta == tb:
        return True
    if ta[0] != tb[0]:
        return False
    for ga, pa, ma in _readings(ta):
        for gb, pb, mb in _readings(tb):
            if pa != pb or len(pa) < 3:
                continue
            if ma and mb and ma != mb:
                continue
            if _given_compatible(ga, gb):
                return True
    return False


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
