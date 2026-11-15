"""Official-directory adapters: bulk, deterministic ingestion of officeholders from primary sources.

Where a government publishes a complete official roster (a legislature's member list, a national
register), fetching it with Exa Contents and parsing it is far cheaper and more reliable than asking an
agent to rediscover hundreds of names. The roster page itself is the evidence (authority tier 1), so each
person is persisted as `official_single` with the page linked via SUPPORTED_BY.

Adapters are registered per country in ADAPTERS. Mexico:
  * mx_federal_deputies   Chamber of Deputies SITL roster (500: 300 district + 200 proportional list)
  * mx_senators           Senate roster by election principle (64 majority + 32 first minority + 32 list)
  * mx_municipalities     INAFED national register: every municipality, its council and how it chooses
                          its authorities (party elections vs. usos y costumbres). The register's officeholder
                          columns are out of date (2021–2024 terms), so people are NOT taken from it.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Callable

from . import ids
from .evidence import EvidenceStatus, SupportingSource, Verification
from .exa_gateway import ExaGateway
from .graph.store import GraphStore
from .graph.writer import GraphWriter
from .ids import canonical_url, normalize_text, registrable_domain, utcnow
from .logging_setup import get_logger
from .registry import CountryRecord, match_subdivision, subdivisions
from .schemas import OfficeholderX, office_is_elected

log = get_logger("directories")

MX_DEPUTIES_URL = "https://sitl.diputados.gob.mx/LXVI_leg/listado_diputados_gpnp.php"
MX_SENATE_URL = "https://www.senado.gob.mx/66/senadores/por_principio_de_eleccion"
MX_INAFED_URL = ("https://www.datos.gob.mx/dataset/a67f0a5b-5933-4d7f-8d51-53cb874ffd29/resource/"
                 "22dec87e-ec40-45e6-9bf7-3065ad1ad7b5/download/presidentes_municipales.csv")

_SURNAME_PARTICLES = {"de", "del", "la", "las", "los", "y", "san", "santa", "da", "di", "van", "von", "mc"}


def mx_name_from_surname_first(raw: str) -> str:
    """'Abreu Artiñano Rocío Adriana' -> 'Rocío Adriana Abreu Artiñano'. Mexican rosters list paternal and
    maternal surnames first; particles ('de la', 'del') attach to the following word."""
    toks = raw.split()
    surnames: list[str] = []
    i = 0
    for _ in range(2):
        part = []
        while i < len(toks) and toks[i].lower() in _SURNAME_PARTICLES:
            part.append(toks[i])
            i += 1
        if i < len(toks):
            part.append(toks[i])
            i += 1
        if part:
            surnames.append(" ".join(part))
        if len(toks) - i <= 1:   # keep at least one given name
            break
    given = toks[i:]
    if not given:                 # could not split safely: keep as published
        return raw.strip()
    return " ".join(given + surnames)


def _official(url: str, title: str) -> Verification:
    return Verification(EvidenceStatus.official_single,
                        [SupportingSource(url=url, title=title, domain=registrable_domain(url), tier=1,
                                          via="official_directory")],
                        checked_urls=[url], reasons=["listed on the official roster"])


def _page(gw: ExaGateway, url: str, purpose: str, chars: int = 400000) -> dict:
    pages = gw.contents([url], purpose=purpose, text_chars=chars)
    page = pages.get(canonical_url(url))
    if not page or not page.get("text"):
        raise RuntimeError(f"official directory not retrievable via Exa Contents: {url}")
    return page


def _national_body(store: GraphStore, iso2: str, needle: str) -> dict:
    lid = ids.level_id(iso2, "national")
    for _, b in store.edges(lid, "HAS_BODY", "out"):
        if needle in normalize_text(b["name"]) or needle in normalize_text(b.get("name_local") or ""):
            return b
    raise RuntimeError(f"national body matching '{needle}' not found; run the 'national' stage first")


def _set_office(w: GraphWriter, office_id: str, **props) -> str:
    w.node(office_id, "PoliticalOffice", **props)
    return office_id


# ======================================================================== Mexico: deputies
def parse_mx_deputies(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^\|\s*\d+\s+(.+?)\s*\|\s*([^|]+?)\s*\|\s*(Dtto|Circ)\.\s*(\d+)\s*\|", line)
        if m:
            out.append({"raw": m.group(1).strip(), "entity": m.group(2).strip(), "kind": m.group(3),
                        "number": int(m.group(4))})
    return out


def mx_federal_deputies(gw: ExaGateway, store: GraphStore, w: GraphWriter, country: CountryRecord) -> dict:
    page = _page(gw, MX_DEPUTIES_URL, "directory:mx-deputies")
    rows = parse_mx_deputies(page["text"])
    if len(rows) < 400:
        raise RuntimeError(f"deputy roster looks incomplete ({len(rows)} rows); refusing to load")
    body = _national_body(store, "MX", "chamber of deputies")
    generic = next((o for _, o in store.edges(body["id"], "HAS_OFFICE", "out")
                    if normalize_text(o["name"]) in ("deputy", "federal deputy")
                    or o.get("seat_type") == "general"), None)
    district_title = "Federal Deputy (single-member district)"
    district = _set_office(w, generic["id"], name=district_title, aliases=[generic["name"]],
                           selection_method="direct_election", is_elected=True, seats=300, single_seat=False,
                           seat_type="general", selection_details="Plurality vote in one of 300 federal districts",
                           term_length_years=3) if generic else \
        w.office(body["id"], title_en=district_title, title_local="Diputado(a) federal de mayoría relativa",
                 selection_method="direct_election", is_elected=True, seats=300, seat_type="general",
                 single_seat=False, term_length_years=3)
    pr = w.office(body["id"], title_en="Federal Deputy (proportional representation list)",
                  title_local="Diputado(a) federal de representación proporcional", selection_method="direct_election",
                  is_elected=True, seats=200, seat_type="proportional_list", single_seat=False, term_length_years=3,
                  selection_details="Elected by popular vote for party lists in 5 multi-state circumscriptions")
    ver = _official(page.get("url") or MX_DEPUTIES_URL, page.get("title") or "Listado de Diputados LXVI")
    stats = {"district": 0, "list": 0}
    seated: set[str] = set()
    for r in rows:
        name = mx_name_from_surname_first(r["raw"])
        if r["kind"] == "Dtto":
            sub = match_subdivision("MX", r["entity"])
            code = f"{(sub or {}).get('code', r['entity'])}-DF{r['number']:02d}"
            oid, seat, label = district, "general", f"{r['entity']}, federal district {r['number']}"
            stats["district"] += 1
        else:
            code, oid, seat, label = f"CIRC-{r['number']}", pr, "proportional_list", f"Circumscription {r['number']}"
            stats["list"] += 1
        claim = OfficeholderX(full_name=name, term_start="2024-09-01", term_end="2027-08-31")
        res = w.holding("MX", oid, claim, ver, single_seat=False,
                        extra={"seat_type": seat, "constituency_code": code, "constituency_name": label,
                               "roster_name": r["raw"], "home_entity": r["entity"]})
        _alias(store, res["person"], r["raw"])
        seated.add(res["person"])
    stats["removed_not_on_roster"] = reconcile_roster(store, [district, pr], seated, MX_DEPUTIES_URL)
    return {"deputies": len(rows), **stats}


def reconcile_roster(store: GraphStore, office_ids: list[str], on_roster: set[str], source: str) -> int:
    """Anyone still marked as a current holder of these offices but absent from the official roster is set
    to current=false (history kept). Catches resignations on refresh and leftovers from earlier errors."""
    n = 0
    for oid in office_ids:
        for e, person in store.edges(oid, "HOLDS_OFFICE", "in"):
            if e.get("current") and person["id"] not in on_roster:
                store.upsert_edge(person["id"], "HOLDS_OFFICE", oid, {"current": False,
                                  "status": "not_on_current_roster", "reconciled_at": utcnow(),
                                  "reconciled_against": source})
                if e.get("term_id") and store.get_node(e["term_id"]):
                    store.upsert_node(e["term_id"], "Term", {"current": False, "status": "not_on_current_roster"})
                n += 1
    return n


def _alias(store: GraphStore, person_id: str, alias: str) -> None:
    p = store.get_node(person_id)
    if p and alias != p["name"] and alias not in (p.get("aliases") or []):
        store.upsert_node(person_id, "Person", {"aliases": sorted({*(p.get("aliases") or []), alias})})


# ======================================================================== Mexico: senators
_SEN_SECTIONS = [("mayoria relativa", "relative_majority"), ("primera minoria", "first_minority"),
                 ("representacion proporcional", "proportional_list")]


def parse_mx_senators(text: str) -> list[dict]:
    out, section, entity = [], None, None
    entity_names = {normalize_text(x["name"]) for x in subdivisions("MX")}
    entity_names |= {"ciudad de mexico", "estado de mexico", "michoacan", "veracruz", "coahuila", "queretaro"}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            n = normalize_text(s)
            section = next((v for k, v in _SEN_SECTIONS if k in n), None)
            entity = None
            continue
        m = re.match(r"^#{2,4}\s*Sen\.\s*(.+)$", s)
        if m and section:
            out.append({"name": m.group(1).strip(), "section": section, "entity": entity})
            continue
        if s and len(s) < 40 and normalize_text(s) in entity_names:
            entity = s
    return out


def mx_senators(gw: ExaGateway, store: GraphStore, w: GraphWriter, country: CountryRecord) -> dict:
    page = _page(gw, MX_SENATE_URL, "directory:mx-senate")
    rows = parse_mx_senators(page["text"])
    if len(rows) < 100:
        raise RuntimeError(f"senate roster looks incomplete ({len(rows)} rows); refusing to load")
    body = _national_body(store, "MX", "senate")
    generic = next((o for _, o in store.edges(body["id"], "HAS_OFFICE", "out")
                    if normalize_text(o["name"]) == "senator" or o.get("seat_type") == "relative_majority"), None)
    spec = {
        "relative_majority": ("Senator (relative majority)", "Senador(a) de mayoría relativa", 64,
                              "Two seats per state for the most-voted formula"),
        "first_minority": ("Senator (first minority)", "Senador(a) de primera minoría", 32,
                           "One seat per state for the runner-up party's formula"),
        "proportional_list": ("Senator (proportional representation list)", "Senador(a) de representación proporcional",
                              32, "Elected by popular vote for national party lists"),
    }
    offices = {}
    for key, (title, local, seats, details) in spec.items():
        if key == "relative_majority" and generic:
            offices[key] = _set_office(w, generic["id"], name=title, title_local=local, aliases=[generic["name"]],
                                       selection_method="direct_election", is_elected=True, seats=seats,
                                       seat_type=key, single_seat=False, selection_details=details,
                                       term_length_years=6)
        else:
            offices[key] = w.office(body["id"], title_en=title, title_local=local, selection_method="direct_election",
                                    is_elected=True, seats=seats, seat_type=key, single_seat=False,
                                    selection_details=details, term_length_years=6)
    ver = _official(page.get("url") or MX_SENATE_URL, page.get("title") or "Senado — Por principio de elección")
    stats: dict[str, int] = {}
    seated: set[str] = set()
    for r in rows:
        sub = match_subdivision("MX", r["entity"]) if r["entity"] else None
        code = (sub or {}).get("code") if r["section"] != "proportional_list" else "NATIONAL-LIST"
        claim = OfficeholderX(full_name=r["name"], term_start="2024-09-01", term_end="2030-08-31")
        res = w.holding("MX", offices[r["section"]], claim, ver, single_seat=False,
                        extra={"seat_type": r["section"], "constituency_code": code,
                               "constituency_name": r["entity"] or "National list"})
        seated.add(res["person"])
        stats[r["section"]] = stats.get(r["section"], 0) + 1
    stats["removed_not_on_roster"] = reconcile_roster(store, list(offices.values()), seated, MX_SENATE_URL)
    return {"senators": len(rows), **stats}


# ======================================================================== Mexico: municipalities
def mx_municipalities(gw: ExaGateway, store: GraphStore, w: GraphWriter, country: CountryRecord) -> dict:
    page = _page(gw, MX_INAFED_URL, "directory:mx-municipalities", chars=1000000)
    rows = list(csv.DictReader(io.StringIO(page["text"])))
    if len(rows) < 2000:
        raise RuntimeError(f"municipal register looks incomplete ({len(rows)} rows); refusing to load")
    mun_lid = ids.level_id("MX", "municipal")
    w.node(mun_lid, "GovernmentLevel", name="Municipal / Local government", tier="municipal", country="MX",
           unit_name="Municipality", unit_name_local="Municipio", unit_count=len(rows),
           council_name="Municipal council", council_name_local="Ayuntamiento",
           researched_name="Municipalities (municipios)", name_local="Municipios",
           register_url=MX_INAFED_URL, register_note="Municipality list and selection system from the INAFED "
           "register; its officeholder columns are 2021–2024 terms and are not used as current officeholders.")
    w.edge(ids.country_id("MX"), "HAS_GOVERNMENT_LEVEL", mun_lid)
    src = SupportingSource(url=MX_INAFED_URL, title="INAFED — Presidentas y Presidentes municipales",
                           domain=registrable_domain(MX_INAFED_URL), tier=1, via="official_directory")
    stats = {"municipalities": 0, "customary": 0, "skipped_cdmx": 0, "unmatched_state": 0}
    for r in rows:
        estado, mun = r["estado"].strip(), r["municipio"].strip()
        if normalize_text(estado) in ("cdmx", "ciudad de mexico"):
            stats["skipped_cdmx"] += 1          # Mexico City boroughs come from the 'special' stage
            continue
        sub = match_subdivision("MX", estado)
        if not sub:
            stats["unmatched_state"] += 1
            continue
        customary = "UYC" in (r.get("partido") or "").upper() or "USOS" in (r.get("descripcion") or "").upper()
        ulid = ids.level_id("MX", "municipal", sub["code"], mun)
        w.node(ulid, "GovernmentLevel", name=f"{mun}, {sub['name']}", tier="municipal_unit", country="MX",
               parent_division=sub["name"], parent_iso_3166_2=sub["code"], inegi_code=r.get("cve_inegi"),
               population=_int(r.get("total_pob")),
               selection_system="usos y costumbres (customary community assembly)" if customary
               else "party-based popular election")
        w.edge(mun_lid, "CONTAINS", ulid)
        body = ids.body_id(ulid, "council")
        w.node(body, "GovernmentBody", name=f"Municipal council of {mun}", name_local=f"Ayuntamiento de {mun}",
               branch="executive", website=(r.get("pag_web") or "").strip() or None)
        w.edge(ulid, "HAS_BODY", body)
        method = "customary_election" if customary else "direct_election"
        oid = w.office(body, title_en=f"Municipal President of {mun}", title_local=f"Presidente(a) Municipal de {mun}",
                       selection_method=method, is_elected=True, seats=1, single_seat=True,
                       selection_details="Chosen by community assembly under usos y costumbres" if customary
                       else "Elected by popular vote with the council slate (planilla)")
        w.support(oid, [src], claim="municipality and its selection system (INAFED register)")
        stats["municipalities"] += 1
        stats["customary"] += int(customary)
    return stats


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


ADAPTERS: dict[str, list[Callable]] = {"MX": [mx_federal_deputies, mx_senators, mx_municipalities]}
