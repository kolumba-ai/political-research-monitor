"""Country-faithful hierarchy (see AGENTS.md and structures/<ISO>.md).

Local government is nested under the first-level division that constitutes it:
  Country -> first-level divisions tier -> <division> -> "Municipalities of <division>" -> <municipality>
never under a country-wide municipal tier. Special local regimes (Mexico City's alcaldías) sit under their
division too. Tier/body names and article citations come from each country's approved Structure Spec.

`restructure_local` migrates graphs built with the old country-wide tier; `apply_spec_mx` applies the
approved Mexico spec (structures/MX.md). Both are idempotent and never delete people or office-holdings.
"""
from __future__ import annotations

from . import ids
from .graph.store import GraphStore
from .graph.writer import GraphWriter
from .ids import normalize_text
from .logging_setup import get_logger

log = get_logger("structure")

# Tier names per approved Structure Spec: English with the official term in brackets.
TERMS = {
    "MX": {"national": "Federation (Federación)",
           "state": "Federal entities (Entidades federativas)",
           "local": "Municipalities of {division} (Municipios)",
           "special_local": "Alcaldías of {division} (Demarcaciones territoriales)"},
}
DEFAULT = {"national": "National / Federal government", "state": "State / Provincial government",
           "municipal": "Municipal / Local government", "local": "Local governments of {division}",
           "special_local": "Local government of {division}"}
ARTICLES = {"MX": {"national": "CPEUM arts. 40, 49", "state": "CPEUM art. 43", "local": "CPEUM art. 115",
                   "special_local": "CPEUM art. 122-A-VI", "state_unit": "CPEUM art. 116",
                   "MX-CMX": "CPEUM arts. 44, 122"}}


def tier_name(iso2: str, tier: str) -> str:
    return TERMS.get(iso2.upper(), {}).get(tier) or DEFAULT[tier]


def _article(iso2: str, key: str) -> str | None:
    return ARTICLES.get(iso2.upper(), {}).get(key)


def local_container(w: GraphWriter, iso2: str, division_code: str, division_name: str) -> str:
    """'Municipalities of Jalisco' under the Jalisco node (created on demand). Returns its id."""
    iso2 = iso2.upper()
    state_tier = ids.level_id(iso2, "state")
    state_unit = ids.level_id(iso2, "state", division_code)
    if not w.store.get_node(state_tier):
        w.node(state_tier, "GovernmentLevel", name=tier_name(iso2, "state"), tier="state", country=iso2,
               article=_article(iso2, "state"))
        w.edge(ids.country_id(iso2), "HAS_GOVERNMENT_LEVEL", state_tier)
    if not w.store.get_node(state_unit):
        w.node(state_unit, "GovernmentLevel", name=division_name, tier="state_unit", iso_3166_2=division_code,
               country=iso2)
        w.edge(state_tier, "CONTAINS", state_unit)
    cid = f"{state_unit}:local"
    w.node(cid, "GovernmentLevel", name=tier_name(iso2, "local").format(division=division_name), tier="municipal",
           country=iso2, parent_iso_3166_2=division_code, article=_article(iso2, "local"))
    w.edge(state_unit, "CONTAINS", cid)
    return cid


def special_parent(store: GraphStore, iso2: str, division_code: str | None) -> str | None:
    """Where a special local regime (e.g. Mexico City's alcaldías) hangs: its division's node."""
    if not division_code:
        return None
    sid = ids.level_id(iso2, "state", division_code)
    return sid if store.get_node(sid) else None


def restructure_local(store: GraphStore, iso2: str) -> dict:
    """Move every local unit from a country-wide municipal tier into its division; remove that tier."""
    iso2 = iso2.upper()
    w = GraphWriter(store, "restructure", "restructure")
    mun = ids.level_id(iso2, "municipal")
    stats = {"units_moved": 0, "special_moved": 0, "templates_removed": 0, "left_in_place": [],
             "country_tier_removed": False}
    if not store.get_node(mun):
        return stats
    state_units = {n.get("iso_3166_2"): n for _, n in store.edges(ids.level_id(iso2, "state"), "CONTAINS", "out")
                   if n.get("iso_3166_2")}
    for _, n in store.edges(mun, "CONTAINS", "out"):
        if n.get("tier") == "special_local":
            parts = n["id"].split(":")
            code = parts[3] if len(parts) > 3 and parts[3] in state_units else None
            parent = special_parent(store, iso2, code)
            if not parent:
                stats["left_in_place"].append(n["id"])
                continue
            division = state_units[code]["name"]
            store.delete_edge(mun, "CONTAINS", n["id"])
            store.upsert_edge(parent, "CONTAINS", n["id"], {})
            store.upsert_node(n["id"], "GovernmentLevel", {
                "name": tier_name(iso2, "special_local").format(division=division), "parent_iso_3166_2": code,
                "previous_name": n["name"], "article": _article(iso2, "special_local")})
            stats["special_moved"] += 1
            continue
        code = n.get("parent_iso_3166_2")
        if code not in state_units:
            stats["left_in_place"].append(n["id"])
            continue
        cid = local_container(w, iso2, code, state_units[code]["name"])
        store.delete_edge(mun, "CONTAINS", n["id"])
        store.upsert_edge(cid, "CONTAINS", n["id"], {})
        stats["units_moved"] += 1

    # The generic "standard structure" template is not a constitutional entity: remove it.
    for _, b in store.edges(mun, "HAS_BODY", "out"):
        if b.get("template"):
            for _, o in store.edges(b["id"], "HAS_OFFICE", "out"):
                if not store.edges(o["id"], "HOLDS_OFFICE", "in"):
                    store.delete_node(o["id"])
            store.delete_node(b["id"])
            stats["templates_removed"] += 1
    if not store.edges(mun, "CONTAINS", "out") and not store.edges(mun, "HAS_BODY", "out"):
        store.delete_node(mun)
        stats["country_tier_removed"] = True
    log.info("structure.local_restructured", country=iso2, **{k: v if not isinstance(v, list) else len(v)
                                                              for k, v in stats.items()})
    return stats


# =========================================================================== Mexico spec
_MX_BODIES = {   # existing body slug -> (name, branch, article)
    "chamber-of-deputies": ("Chamber of Deputies (Cámara de Diputados)", "legislative", "CPEUM arts. 50–52"),
    "senate-of-the-republic": ("Senate (Cámara de Senadores)", "legislative", "CPEUM arts. 50, 56"),
    "presidency-and-federal-executive-administration": ("Presidency of the Republic (Poder Ejecutivo Federal)",
                                                         "executive", "CPEUM arts. 80–83, 89"),
    "supreme-court-of-justice-of-the-nation": ("Supreme Court of Justice of the Nation (Suprema Corte de Justicia "
                                               "de la Nación)", "judicial", "CPEUM arts. 94, 96"),
    "federal-electoral-tribunal": ("Electoral Tribunal (Tribunal Electoral del Poder Judicial de la Federación)",
                                   "judicial", "CPEUM arts. 94, 96, 99"),
    "federal-judicial-discipline-tribunal": ("Judicial Discipline Tribunal (Tribunal de Disciplina Judicial)",
                                             "judicial", "CPEUM arts. 94, 96"),
    "federal-judicial-administration-body": ("Judicial Administration Body (Órgano de Administración Judicial)",
                                             "judicial", "CPEUM art. 94"),
    "national-electoral-institute-general-council": ("National Electoral Institute, General Council (Instituto "
                                                     "Nacional Electoral, Consejo General)", "autonomous",
                                                     "CPEUM art. 41-V-A"),
}
_MX_NEW_COURTS = [   # slug, name, description, article
    ("regional-plenaries", "Regional Plenaries (Plenos Regionales)",
     "Federal judicial body listed in art. 94; composition set by the Ley Orgánica del Poder Judicial de la "
     "Federación (not among the offices elected under art. 96).", "CPEUM art. 94"),
    ("collegiate-circuit-courts", "Collegiate Circuit Courts (Tribunales Colegiados de Circuito)", None,
     "CPEUM arts. 94, 96"),
    ("collegiate-appeals-courts", "Collegiate Appeals Courts (Tribunales Colegiados de Apelación)", None,
     "CPEUM arts. 94, 96"),
    ("district-courts", "District Courts (Juzgados de Distrito)", None, "CPEUM arts. 94, 96"),
]
_MX_OFFICES = {   # office slug suffix -> props
    "president-of-mexico": {"title_local": "Presidente(a) de los Estados Unidos Mexicanos", "article": "CPEUM arts. 80, 81, 83"},
    "secretary-of-state": {"name": "Secretary of State (Secretario/a de Estado)", "article": "CPEUM art. 89-II"},
    "deputy": {"article": "CPEUM art. 52"},
    "federal-deputy-proportional-representation-list": {"article": "CPEUM art. 52"},
    "senator": {"article": "CPEUM art. 56"},
    "senator-first-minority": {"article": "CPEUM art. 56"},
    "senator-proportional-representation-list": {"article": "CPEUM art. 56"},
    "justice-of-the-supreme-court": {"name": "Minister of the Supreme Court (Ministro/a)", "seats": 9,
                                     "article": "CPEUM arts. 94, 96"},
    "president-of-the-supreme-court": {"selection_details": "Rotates every two years to the most-voted ministers "
                                       "in the judicial election", "article": "CPEUM art. 94"},
    "electoral-magistrate-superior-chamber": {"article": "CPEUM art. 96"},
    "electoral-magistrate-regional-chambers": {"article": "CPEUM art. 96"},
    "judicial-discipline-magistrate": {"article": "CPEUM art. 96"},
    "electoral-councillor": {"article": "CPEUM art. 41-V-A"},
    "president-of-the-general-council": {"article": "CPEUM art. 41-V-A"},
}


def apply_spec_mx(store: GraphStore) -> dict:
    """Apply structures/MX.md (approved 2026-11-15): tier names, art. 94 federal judiciary, citations."""
    w = GraphWriter(store, "structure_spec", "spec-MX-2026-11-15")
    stats = {"renamed": 0, "courts_added": 0, "offices_moved": 0, "bodies_removed": 0}
    nat, sta = ids.level_id("MX", "national"), ids.level_id("MX", "state")
    store.upsert_node(nat, "GovernmentLevel", {"name": tier_name("MX", "national"), "article": _article("MX", "national")})
    store.upsert_node(sta, "GovernmentLevel", {"name": tier_name("MX", "state"), "article": _article("MX", "state")})
    for _, unit in store.edges(sta, "CONTAINS", "out"):
        key = unit.get("iso_3166_2") if unit.get("iso_3166_2") == "MX-CMX" else "state_unit"
        store.upsert_node(unit["id"], "GovernmentLevel", {"article": _article("MX", key)})

    bodies = {b["id"].split(":")[-1]: b for _, b in store.edges(nat, "HAS_BODY", "out")}
    for slug, (name, branch, art) in _MX_BODIES.items():
        if slug in bodies:
            store.upsert_node(bodies[slug]["id"], "GovernmentBody", {"name": name, "branch": branch, "article": art})
            stats["renamed"] += 1
    new_ids = {}
    for slug, name, desc, art in _MX_NEW_COURTS:
        bid = ids.body_id(nat, slug)
        w.node(bid, "GovernmentBody", name=name, branch="judicial", description=desc, article=art,
               spec="structures/MX.md")
        w.edge(nat, "HAS_BODY", bid)
        new_ids[slug] = bid
        stats["courts_added"] += 1

    # Split the former combined "circuit courts and district courts" body along art. 94.
    old = bodies.get("federal-circuit-courts-and-district-courts")
    if old:
        for _, o in store.edges(old["id"], "HAS_OFFICE", "out"):
            # Decide by the office's own slug: the id also contains the old body's slug ("…-district-courts").
            own = o["id"].split(":")[-1]
            target = new_ids["district-courts"] if "district" in own else new_ids["collegiate-circuit-courts"]
            store.delete_edge(old["id"], "HAS_OFFICE", o["id"])
            store.upsert_edge(target, "HAS_OFFICE", o["id"], {})
            store.upsert_node(o["id"], "PoliticalOffice", {"article": "CPEUM art. 96"})
            stats["offices_moved"] += 1
        if not store.edges(old["id"], "HAS_OFFICE", "out"):
            store.delete_node(old["id"])
            stats["bodies_removed"] += 1
    w.office(new_ids["collegiate-appeals-courts"], title_en="Circuit magistrate (Collegiate Appeals Court)",
             title_local="Magistrado(a) de Circuito", selection_method="direct_election", is_elected=True,
             single_seat=False, article="CPEUM art. 96",
             selection_details="Elected by free, direct and secret popular vote (art. 96)")

    for _, b in store.edges(nat, "HAS_BODY", "out"):
        for _, o in store.edges(b["id"], "HAS_OFFICE", "out"):
            props = next((v for k, v in _MX_OFFICES.items() if o["id"].endswith(":" + k)), None)
            if props:
                store.upsert_node(o["id"], "PoliticalOffice", dict(props))
    log.info("structure.spec_applied", country="MX", **stats)
    return stats
