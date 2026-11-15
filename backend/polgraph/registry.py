"""Seed country registry from ISO 3166-1 + UN M49 regional codes (not from search results).

Dataset: lukes/ISO-3166-Countries-with-Regional-Codes (all.csv), vendored in polgraph/data/.
Subdivisions (ISO 3166-2) come from the `pycountry` package.
"""
from __future__ import annotations

import csv
import difflib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pycountry

from .ids import normalize_text

DATA = Path(__file__).parent / "data" / "iso3166_m49.csv"


@dataclass(frozen=True)
class CountryRecord:
    name: str
    iso2: str
    iso3: str
    m49: str
    region: str
    sub_region: str
    intermediate_region: str
    continent: str


_BLANK_REGION_FALLBACK = {"TW": "Asia"}


def _continent(region: str, sub_region: str, intermediate: str, iso2: str) -> str:
    """Map UN M49 regions onto the conventional seven-continent model."""
    if iso2 == "AQ":
        return "Antarctica"
    if not region and iso2 in _BLANK_REGION_FALLBACK:  # dataset leaves the UN region blank for these
        return _BLANK_REGION_FALLBACK[iso2]
    if region == "Americas":
        if intermediate == "South America":
            return "South America"
        return "North America"  # Northern America, Central America, Caribbean
    return region or "Unclassified"


@lru_cache
def load_registry() -> dict[str, CountryRecord]:
    out: dict[str, CountryRecord] = {}
    with DATA.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rec = CountryRecord(
                name=row["name"],
                iso2=row["alpha-2"],
                iso3=row["alpha-3"],
                m49=row["country-code"],
                region=row["region"],
                sub_region=row["sub-region"],
                intermediate_region=row["intermediate-region"],
                continent=_continent(row["region"], row["sub-region"], row["intermediate-region"], row["alpha-2"]),
            )
            out[rec.iso2] = rec
    return out


def get_country(iso2: str) -> CountryRecord:
    reg = load_registry()
    try:
        return reg[iso2.upper()]
    except KeyError as exc:
        raise KeyError(f"Unknown ISO 3166-1 alpha-2 code: {iso2}") from exc


def subdivisions(iso2: str) -> list[dict]:
    """Top-level ISO 3166-2 subdivisions (no parent) for a country."""
    subs = pycountry.subdivisions.get(country_code=iso2.upper()) or []
    return [
        {"code": s.code, "name": s.name, "type": s.type}
        for s in subs
        if getattr(s, "parent_code", None) is None
    ]


# Common English/alternate names that differ from the ISO 3166-2 local name.
_ALIASES = {
    "MX-CMX": ["mexico city", "cdmx", "ciudad de mexico", "distrito federal"],
    "MX-MEX": ["state of mexico", "estado de mexico", "edomex"],
    "MX-MIC": ["michoacan", "michoacan de ocampo"],
    "MX-VER": ["veracruz", "veracruz de ignacio de la llave"],
    "MX-COA": ["coahuila", "coahuila de zaragoza"],
    "MX-NLE": ["nuevo leon"],
    "MX-BCN": ["baja california"],
    "MX-BCS": ["baja california sur"],
    "PK-IS": ["islamabad capital territory", "ict", "federal capital territory"],
    "PK-KP": ["kpk", "kp", "khyber pakhtunkhwa", "nwfp"],
    "PK-JK": ["azad kashmir", "ajk", "azad jammu kashmir", "azad jammu and kashmir"],
    "PK-GB": ["gilgit baltistan"],
    "PK-BA": ["baluchistan", "balochistan"],
}


def match_subdivision(iso2: str, name: str, cutoff: float = 0.82) -> dict | None:
    """Resolve a researched subdivision name to its ISO 3166-2 record (accent/alias tolerant)."""
    subs = subdivisions(iso2)
    target = normalize_text(name)
    for prefix in ("estado de ", "state of ", "province of ", "provincia de "):
        if target.startswith(prefix) and target != "estado de mexico":
            target = target[len(prefix):]
    for suffix in (" province", " state", " territory"):
        if target.endswith(suffix) and target[: -len(suffix)] in {normalize_text(s["name"]) for s in subs}:
            target = target[: -len(suffix)]
    by_norm = {normalize_text(s["name"]): s for s in subs}
    for code, aliases in _ALIASES.items():
        if code.startswith(iso2.upper() + "-") and target in aliases:
            return next((s for s in subs if s["code"] == code), None)
    if target in by_norm:
        return by_norm[target]
    best = difflib.get_close_matches(target, list(by_norm), n=1, cutoff=cutoff)
    return by_norm[best[0]] if best else None
