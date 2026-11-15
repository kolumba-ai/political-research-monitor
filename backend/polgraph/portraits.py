"""Politician portraits found with Exa Search and checked before they are stored on the `Person` node.

One search per person (`<name> <office>` on Wikipedia, `extras.image_links`), then one on official domains if that
finds nothing. A picture is accepted only when the page title names the person (given name + a surname) and the
image file name carries one of the person's surnames, so coats of arms, party logos and other people's photos on
the same page are skipped. Stored as `image_url` (+ source page, domain, method, retrieval time). We store links,
not files: Wikimedia Commons images carry their own licence on the source page.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .evidence import mentions_name
from .graph.store import GraphStore
from .ids import name_tokens, normalize_text, utcnow
from .logging_setup import get_logger

log = get_logger("portraits")

WIKI_DOMAINS = ["es.wikipedia.org", "en.wikipedia.org"]
OFFICIAL_DOMAINS = {"MX": ["gob.mx"], "PK": ["gov.pk"]}
NOT_PORTRAIT = re.compile(r"(logo|escudo|coat[_ -]?of[_ -]?arms|seal|flag|bandera|icon|mapa|map_|locator|firma|"
                          r"signature|banner|placeholder|\.svg)", re.I)
OFFICE_RANK = (("president", 0), ("head-of-government", 0), ("governor", 1), ("senat", 2), ("deput", 3))


def _surnames(name: str) -> list[str]:
    toks = name_tokens(name)
    return [t for t in toks[1:] if len(t) > 2] or toks[-1:]


def _file_name(url: str) -> str:
    path = unquote(urlparse(url).path)
    return normalize_text(path.rsplit("/", 1)[-1])


def portrait_url(url: str) -> str:
    """Wikimedia thumbnails: drop tracking parameters and ask for 330 px (Wikimedia only serves standard thumbnail widths)."""
    if "wikimedia.org" in url:
        url = url.split("?", 1)[0]
        url = re.sub(r"/\d+px-", "/330px-", url).replace("://thumb.wikimedia.org/", "://upload.wikimedia.org/")
    return url


def pick_portrait(name: str, results: list[dict]) -> dict | None:
    surnames = _surnames(name)
    for r in results:
        title = r.get("title") or ""
        if not mentions_name(name, title):
            continue
        for img in [*(r.get("image_links") or []), *([r["image"]] if r.get("image") else [])]:
            fname = _file_name(img)
            if NOT_PORTRAIT.search(unquote(img)) or not any(s in fname.split() or s in fname for s in surnames):
                continue
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
                   ids: list[str] | None = None) -> dict:
    country = country.upper()
    people = [p for p in store.nodes_by_label("Person") if p["id"].startswith(f"person:{country}:")]
    if ids:
        people = [p for p in people if p["id"] in set(ids)]
    if not force:
        people = [p for p in people if not p.get("image_status")]
    ranked = sorted(((_office(store, p["id"]), p) for p in people), key=lambda x: (x[0][0], x[1].get("name") or ""))
    if limit:
        ranked = ranked[:limit]
    found = missing = 0
    for (_, office), p in ranked:
        name = p.get("name") or ""
        hit = None
        for domains, method in ((WIKI_DOMAINS, "wikipedia"), (OFFICIAL_DOMAINS.get(country), "official_page")):
            if not domains:
                continue
            results = gw.search(f"{name} {office}".strip(), purpose=f"portrait:{p['id']}", num_results=5,
                                include_domains=domains, text_chars=300, highlights=False, image_links=6)
            hit = pick_portrait(name, results)
            if hit:
                hit["image_method"] = method
                break
        props = {"image_status": "found" if hit else "not_found", "image_checked_at": utcnow(), **(hit or {})}
        if hit:
            props["image_retrieved_at"] = props["image_checked_at"]
            found += 1
        else:
            missing += 1
        store.upsert_node(p["id"], "Person", props)
        log.info("portrait", person=p["id"], found=bool(hit), source=(hit or {}).get("image_source_domain"))
    return {"country": country, "checked": found + missing, "found": found, "not_found": missing}
