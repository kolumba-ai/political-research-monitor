"""World-wide regime classification for every country in the registry.

Researching 249 countries one Agent run at a time would cost ~$180, so this stage uses Exa Contents to
fetch whole-world compilation tables in a handful of calls and parses them deterministically:

  * EIU Democracy Index (latest year column)  -> Full democracy / Flawed democracy / Hybrid regime / Authoritarian
  * Freedom House Freedom in the World status  -> Free / Partly free / Not free

Rows are matched to ISO 3166-1 countries; each becomes a DemocracyAssessment (verification =
"secondary_compilation", evidence = the compilation page) and the Country gets a unified
`regime_group` (Democratic / Partly democratic / Authoritarian / Unclassified). Assessments already
verified on a publisher's own site (from the per-country `democracy` stage) are never downgraded and
take precedence. Classification is informational: it does not gate or hide anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pycountry

from . import ids
from .exa_gateway import ExaGateway
from .graph.store import GraphStore
from .graph.writer import GraphWriter
from .ids import normalize_text, utcnow
from .logging_setup import get_logger
from .registry import load_registry

log = get_logger("world")

EIU_URL = "https://en.wikipedia.org/wiki/The_Economist_Democracy_Index"
FH_URL = "https://en.wikipedia.org/wiki/Freedom_in_the_World"

EIU_LABELS = {"full democracy": "full_democracy", "flawed democracy": "flawed_democracy",
              "hybrid regime": "hybrid_regime", "authoritarian": "authoritarian",
              "authoritarian regime": "authoritarian"}
FH_LABELS = {"free": "free", "partly free": "partly_free", "not free": "not_free"}

# canonical label -> (display class, group)
CLASS = {
    "full_democracy": ("Full democracy", "Democratic"),
    "flawed_democracy": ("Flawed democracy", "Democratic"),
    "hybrid_regime": ("Hybrid regime", "Partly democratic"),
    "authoritarian": ("Authoritarian", "Authoritarian"),
    "free": ("Free", "Democratic"),
    "partly_free": ("Partly free", "Partly democratic"),
    "not_free": ("Not free", "Authoritarian"),
    "liberal_democracy": ("Liberal democracy", "Democratic"),
    "electoral_democracy": ("Electoral democracy", "Democratic"),
    "electoral_autocracy": ("Electoral autocracy", "Partly democratic"),
    "closed_autocracy": ("Closed autocracy", "Authoritarian"),
}
INDEX_NAME = {"eiu": "EIU Democracy Index", "freedom_house": "Freedom House Freedom in the World",
              "vdem": "V-Dem Regimes of the World"}
PRIORITY = ["eiu", "freedom_house", "vdem"]   # EIU's typology maps 1:1 onto the requested classes

# Names used by the compilations that ISO / pycountry lookups don't resolve.
ALIASES = {
    "russia": "RU", "iran": "IR", "syria": "SY", "vietnam": "VN", "laos": "LA", "bolivia": "BO",
    "venezuela": "VE", "tanzania": "TZ", "south korea": "KR", "north korea": "KP", "moldova": "MD",
    "czech republic": "CZ", "czechia": "CZ", "turkey": "TR", "turkiye": "TR", "ivory coast": "CI",
    "cote d ivoire": "CI", "cape verde": "CV", "democratic republic of the congo": "CD", "dr congo": "CD",
    "congo kinshasa": "CD", "republic of the congo": "CG", "congo brazzaville": "CG", "congo": "CG",
    "east timor": "TL", "timor leste": "TL", "eswatini": "SZ", "swaziland": "SZ", "micronesia": "FM",
    "brunei": "BN", "taiwan": "TW", "palestine": "PS", "the gambia": "GM", "gambia": "GM", "bahamas": "BS",
    "the bahamas": "BS", "united states": "US", "united kingdom": "GB", "macedonia": "MK",
    "north macedonia": "MK", "burma": "MM", "myanmar": "MM", "hong kong": "HK", "macau": "MO",
    "saint kitts and nevis": "KN", "saint lucia": "LC", "saint vincent and the grenadines": "VC",
    "sao tome and principe": "ST", "vatican city": "VA", "holy see": "VA", "kyrgyzstan": "KG",
    "slovakia": "SK", "netherlands": "NL", "uae": "AE", "united arab emirates": "AE", "guinea bissau": "GW",
    "puerto rico": "PR", "greenland": "GL", "western sahara": "EH", "gaza strip": "PS", "west bank": "PS",
    "congo democratic republic": "CD", "congo republic": "CG",
}
# Entities with no ISO 3166-1 code in the registry: never map them to a neighbour by fuzzy matching.
NO_ISO = {"kosovo", "abkhazia", "south ossetia", "transnistria", "northern cyprus", "somaliland",
          "nagorno karabakh", "artsakh", "crimea", "indian kashmir", "pakistani kashmir", "tibet",
          "eastern donbas", "countries"}


def resolve_iso2(name: str) -> str | None:
    n = normalize_text(re.sub(r"\[[^\]]*\]|\*|†", "", name))
    if not n:
        return None
    if n in NO_ISO:
        return None
    if n in ALIASES:
        return ALIASES[n]
    # Exact lookups only (name, official name, common name, codes). No fuzzy matching: it maps e.g.
    # "Kosovo" to Serbia, which would silently overwrite another country's rating.
    try:
        return pycountry.countries.lookup(name.strip()).alpha_2
    except LookupError:
        return None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_eiu(text: str) -> tuple[dict[str, tuple[str, float | None]], int | None]:
    """First occurrence per country of `| Country | Regime type | <latest score> | …` rows."""
    year = None
    m = re.search(r"\|\s*Country\s*\|\s*Regime type\s*\|\s*(\d{4})", text)
    if m:
        year = int(m.group(1))
    out: dict[str, tuple[str, float | None]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        for i, c in enumerate(cells):
            lab = EIU_LABELS.get(normalize_text(c))
            if lab and i > 0 and cells[i - 1] and not re.fullmatch(r"[\d=]+", cells[i - 1]):
                country = cells[i - 1]
                score = None
                if i + 1 < len(cells):
                    try:
                        score = float(cells[i + 1])
                    except ValueError:
                        score = None
                out.setdefault(country, (lab, score))
                break
    return out, year


def parse_fh(text: str) -> tuple[dict[str, tuple[str, float | None, int | None]], list[int]]:
    """Rows ending `| Total | PR | CL | Overall status |` (countries and territories) with the
    edition year taken from the nearest preceding '#### YYYY …' heading."""
    out: dict[str, tuple[str, float | None, int | None]] = {}
    years: list[int] = []
    year = None
    for line in text.splitlines():
        h = re.match(r"#+\s*(\d{4})\b", line)
        if h:
            year = int(h.group(1))
            years.append(year)
            continue
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        # Countries: | name | electoral dem. | PR | CL | Total | PR | CL | status |; territories omit one column.
        if len(cells) >= 7 and normalize_text(cells[-1]) in FH_LABELS:
            try:
                total = float(cells[-4])
            except ValueError:
                total = None
            out.setdefault(cells[0], (FH_LABELS[normalize_text(cells[-1])], total, year))
    return out, sorted(set(years))


@dataclass
class WorldResult:
    matched: dict[str, int]
    unmatched: dict[str, list[str]]
    groups: dict[str, int]


def classify_world(gw: ExaGateway, store: GraphStore, ttl_hours: float | None = None) -> WorldResult:
    pages = gw.contents([EIU_URL, FH_URL], purpose="world-classify", text_chars=400000, ttl_hours=ttl_hours)
    eiu_page = pages.get(ids.canonical_url(EIU_URL))
    fh_page = pages.get(ids.canonical_url(FH_URL))
    if not eiu_page and not fh_page:
        raise RuntimeError("could not fetch either compilation table via Exa Contents")
    eiu, eiu_year = parse_eiu(eiu_page["text"]) if eiu_page else ({}, None)
    fh, fh_years = parse_fh(fh_page["text"]) if fh_page else ({}, [])
    log.info("world.parsed", eiu_rows=len(eiu), eiu_year=eiu_year, fh_rows=len(fh), fh_years=fh_years)

    registry = load_registry()
    w = GraphWriter(store, "world_classification", f"world-{utcnow()}")
    matched = {"eiu": 0, "freedom_house": 0}
    unmatched: dict[str, list[str]] = {"eiu": [], "freedom_house": []}

    def record(index: str, iso2: str, label: str, score: float | None, year: int | None, url: str, title: str):
        aid = ids.assessment_id(iso2, index, year)
        existing = store.get_node(aid)
        verified = bool(existing and existing.get("verification") == "verified")
        props = dict(name=f"{INDEX_NAME[index]} {year or ''}: {CLASS[label][0]}".strip(), index=index,
                     classification=label, classification_raw=CLASS[label][0], score=score, assessment_year=year,
                     evidence_url=existing.get("evidence_url") if verified else url,
                     compilation_url=url, last_verified=utcnow())
        if not verified:
            props.update(verification="secondary_compilation", evidence_status="single_source", confidence=0.7,
                         grey_zone=False)
        w.node(aid, "DemocracyAssessment", **props)
        w.edge(ids.country_id(iso2), "HAS_ASSESSMENT", aid)
        w.support(aid, [url], via="compilation_table", claim=f"{index} classifies {iso2} as {label} ({year})")

    for index, rows, url, title in [("eiu", {k: (v[0], v[1], eiu_year) for k, v in eiu.items()}, EIU_URL, "EIU"),
                                    ("freedom_house", fh, FH_URL, "FH")]:
        seen: set[str] = set()
        for name, (label, score, year) in rows.items():
            iso2 = resolve_iso2(name)
            if not iso2 or iso2 not in registry:
                unmatched[index].append(name)
                continue
            if iso2 in seen:      # same country spelled twice (e.g. "Gambia" / "The Gambia"): first row wins
                continue
            seen.add(iso2)
            record(index, iso2, label, score, year, url, title)
            matched[index] += 1

    groups: dict[str, int] = {}
    for iso2, rec in registry.items():
        cid = ids.country_id(iso2)
        by_index: dict[str, dict] = {}
        for _, a in store.edges(cid, "HAS_ASSESSMENT", "out"):
            lab = a.get("classification")
            if lab not in CLASS:
                continue
            rank = (1 if a.get("verification") == "verified" else 0, a.get("assessment_year") or 0)
            cur = by_index.get(a["index"])
            if cur is None or rank > cur["_rank"]:
                by_index[a["index"]] = {**a, "_rank": rank}
        per_source = {k: {"class": CLASS[v["classification"]][0], "group": CLASS[v["classification"]][1],
                          "year": v.get("assessment_year"), "verification": v.get("verification"),
                          "evidence_url": v.get("evidence_url")}
                      for k, v in by_index.items()}
        primary = next((k for k in PRIORITY if k in by_index), None)
        if primary:
            p = per_source[primary]
            group, klass = p["group"], p["class"]
            basis = f"{INDEX_NAME[primary]} {p['year'] or ''}".strip()
            agree = len({v["group"] for v in per_source.values()}) == 1
        else:
            group, klass, basis, agree = "Unclassified", None, "no index covers this country/territory", None
        store.upsert_node(cid, "Country", {
            "regime_group": group, "regime_class": klass, "regime_basis": basis,
            "regime_by_source": per_source or None, "regime_sources_agree": agree,
            "regime_classified_at": utcnow()})
        groups[group] = groups.get(group, 0) + 1
    log.info("world.classified", matched=matched, groups=groups,
             unmatched={k: len(v) for k, v in unmatched.items()})
    return WorldResult(matched, unmatched, groups)
