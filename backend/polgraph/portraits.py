"""Politician portraits found with Exa Search and checked before they are stored on the `Person` node.

One search per person (`<name> <office>` on Wikipedia, `extras.image_links`), then one on official domains if that
finds nothing. A picture is accepted only when the page title is this person's name and no one else's, the page
says they are in politics, and the image file name carries one of their surnames, so coats of arms, party logos,
namesakes and other people's photos are skipped. A missing portrait is preferred to a wrong one. Stored as `image_url` (+ source page, domain, method, retrieval time). We store links,
not files: Wikimedia Commons images carry their own licence on the source page.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin, urlparse

from .graph.store import GraphStore
from .ids import name_tokens, normalize_text, utcnow
from .logging_setup import get_logger

log = get_logger("portraits")

WIKI_DOMAINS = ["es.wikipedia.org", "en.wikipedia.org"]
OFFICIAL_DOMAINS = {"MX": ["gob.mx"], "PK": ["gov.pk"]}
NOT_PORTRAIT = re.compile(r"(logo|escudo|coat[_ -]?of[_ -]?arms|seal|flag|bandera|icon|mapa|map_|locator|firma|"
                          r"signature|banner|placeholder|\.svg)", re.I)
OFFICE_RANK = (("president", 0), ("head-of-government", 0), ("governor", 1), ("senat", 2), ("deput", 3))


POLITICAL = re.compile(r"(politic|diputad|senador|gobernador|president|alcald|legislador|congres|ministr|secretari|"
                       r"partido|party|governor|deputy|senator|mayor)")
TITLE_SPLIT = re.compile(r"\s+[-–—·]\s+|[|:]")


def _name_parts(name: str) -> tuple[list[str], str, str | None]:
    """(given names, paternal surname, maternal surname) under the Spanish order used by the rosters."""
    toks = name_tokens(name)
    if len(toks) >= 3:
        return toks[:-2], toks[-2], toks[-1]
    return toks[:1], toks[-1] if toks else "", None


def page_names_person(name: str, title: str) -> bool:
    """A title segment must be this person's name and nobody else's: a given name plus the paternal surname, and
    no name token the person does not have (so *Laura Rojas Hernández* is not *Laura Hernández García*). A
    disambiguator in brackets must be political (*(político)*, not *(futbolista)*)."""
    given, paternal, maternal = _name_parts(name)
    allowed = {*given, paternal, *([maternal] if maternal else [])}
    brackets = re.findall(r"\(([^)]*)\)", title or "")
    if brackets and not any(POLITICAL.search(normalize_text(b)) for b in brackets):
        return False
    for segment in TITLE_SPLIT.split(re.sub(r"\([^)]*\)", " ", title or "")):
        toks = name_tokens(segment)
        if toks and set(toks) <= allowed and paternal in toks and any(g in toks for g in given):
            return True
    return False


def file_names_person(name: str, image_url: str, *, full: bool = False) -> bool:
    """The image file carries a surname; with `full`, a given name and every surname (roster photo file names)."""
    given, paternal, maternal = _name_parts(name)
    words = set(re.findall(r"[a-z]+", normalize_text(unquote(urlparse(image_url).path).rsplit("/", 1)[-1])))
    if full:
        return paternal in words and (maternal is None or maternal in words) and any(g in words for g in given)
    return paternal in words or (maternal is not None and maternal in words)


def portrait_url(url: str) -> str:
    """Wikimedia thumbnails: drop tracking parameters and ask for 330 px (Wikimedia only serves standard thumbnail widths)."""
    if "wikimedia.org" in url:
        url = url.split("?", 1)[0]
        url = re.sub(r"/\d+px-", "/330px-", url).replace("://thumb.wikimedia.org/", "://upload.wikimedia.org/")
    return url


def pick_portrait(name: str, results: list[dict], *, official: bool = False) -> dict | None:
    """Wikipedia: the page must be about this person, say they are in politics, and the file must carry a surname.
    Official domains: the same page rule, or a file name with the person's full name (roster listings)."""
    for r in results:
        page_ok = page_names_person(name, r.get("title") or "") and (
            official or bool(POLITICAL.search(normalize_text(r.get("text") or ""))))
        for img in [*(r.get("image_links") or []), *([r["image"]] if r.get("image") else [])]:
            img = urljoin(r.get("url") or "", img)  # pages may list relative or protocol-relative image paths
            if not img.startswith(("http://", "https://")) or NOT_PORTRAIT.search(unquote(img)):
                continue
            if (page_ok and file_names_person(name, img)) or (official and file_names_person(name, img, full=True)):
                return {"image_url": portrait_url(img), "image_source_url": r.get("url"),
                        "image_source_domain": urlparse(r.get("url") or "").netloc.removeprefix("www.")}
    return None


def _office(store: GraphStore, pid: str) -> tuple[int, str]:
    best = (9, "")
    for e, o in store.edges(pid, "HOLDS_OFFICE", "out"):
        if not e.get("current"):
            continue
        rank = 4 if ":municipal" in o["id"] else next((r for k, r in OFFICE_RANK if k in o["id"]), 5)
        best = min(best, (rank, o.get("title_local") or o.get("name") or ""))
    return best


def find_portraits(gw, store: GraphStore, country: str, *, limit: int | None = None, force: bool = False,
                   ids: list[str] | None = None, workers: int = 1) -> dict:
    """`workers` people are searched at once; Exa calls still share the gateway's rate limit and budget."""
    country = country.upper()
    people = [p for p in store.nodes_by_label("Person") if p["id"].startswith(f"person:{country}:")]
    if ids:
        people = [p for p in people if p["id"] in set(ids)]
    if not force:
        people = [p for p in people if not p.get("image_status")]
    ranked = sorted(((_office(store, p["id"]), p) for p in people), key=lambda x: (x[0][0], x[1].get("name") or ""))
    if limit:
        ranked = ranked[:limit]
    def check(item) -> bool:
        (_, office), p = item
        name = p.get("name") or ""
        hit = None
        for domains, method in ((WIKI_DOMAINS, "wikipedia"), (OFFICIAL_DOMAINS.get(country), "official_page")):
            if not domains:
                continue
            results = gw.search(f"{name} {office}".strip(), purpose=f"portrait:{p['id']}", num_results=5,
                                include_domains=domains, text_chars=300, highlights=False, image_links=6)
            hit = pick_portrait(name, results, official=method == "official_page")
            if hit:
                hit["image_method"] = method
                break
        empty = {"image_url": "", "image_source_url": "", "image_source_domain": "", "image_method": ""}
        props = {**empty, "image_status": "found" if hit else "not_found", "image_checked_at": utcnow(), **(hit or {})}
        if hit:
            props["image_retrieved_at"] = props["image_checked_at"]
        store.upsert_node(p["id"], "Person", props)
        log.info("portrait", person=p["id"], found=bool(hit), source=(hit or {}).get("image_source_domain"))
        return bool(hit)

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(check, ranked))
    else:
        results = [check(item) for item in ranked]
    found = sum(results)
    return {"country": country, "checked": len(results), "found": found, "not_found": len(results) - found}
