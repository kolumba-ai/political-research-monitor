"""Strict extraction schemas.

Each Pydantic model here does double duty:
  * `agent_schema(Model)` produces the JSON Schema passed to Exa Agent as `outputSchema`;
  * `Model.model_validate(run.output.structured)` validates what came back before anything
    is allowed near the graph.
Validators reject placeholders ("unknown", "vacant", "N/A") instead of letting them become
fake people, and normalise dates to ISO (YYYY, YYYY-MM or YYYY-MM-DD) or None.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ids import normalize_text


class SelectionMethod(str, Enum):
    direct_election = "direct_election"
    indirect_election = "indirect_election"
    appointment = "appointment"
    mixed = "mixed"
    hereditary = "hereditary"
    ex_officio = "ex_officio"
    unknown = "unknown"


def office_is_elected(method: SelectionMethod | str | None) -> Optional[bool]:
    """Whether the *office* is filled by election. Says nothing about who may run for it."""
    m = SelectionMethod(method) if method else SelectionMethod.unknown
    if m in (SelectionMethod.direct_election, SelectionMethod.indirect_election):
        return True
    if m in (SelectionMethod.appointment, SelectionMethod.hereditary, SelectionMethod.ex_officio):
        return False
    return None  # mixed / unknown: cannot say without per-seat detail


class Branch(str, Enum):
    executive = "executive"
    legislative = "legislative"
    judicial = "judicial"
    electoral = "electoral"
    autonomous = "autonomous"
    other = "other"


class ContactStatus(str, Enum):
    allowed = "allowed"
    restricted = "restricted"
    prohibited = "prohibited"
    unknown = "unknown"


class Purpose(str, Enum):
    issue_communication = "issue_communication"
    fact_verification_request = "fact_verification_request"
    petition = "petition"
    lobbying = "lobbying"
    campaign_communication = "campaign_communication"


class Channel(str, Enum):
    email = "email"
    postal_mail = "postal_mail"
    phone = "phone"
    web_form = "web_form"
    social_media = "social_media"
    in_person = "in_person"
    any = "any"


class Sender(str, Enum):
    citizen = "citizen"
    foreign_national = "foreign_national"
    organization = "organization"
    foreign_organization = "foreign_organization"
    automated_system = "automated_system"
    any = "any"


class Recipient(str, Enum):
    elected_official = "elected_official"
    candidate = "candidate"
    public_servant = "public_servant"
    any = "any"


class LawCategory(str, Enum):
    constitutional = "constitutional"
    privacy = "privacy"
    election = "election"
    lobbying = "lobbying"
    communications = "communications"
    other = "other"


_PLACEHOLDERS = {"", "unknown", "n a", "na", "none", "null", "vacant", "tbd", "to be determined", "not found",
                 "not available", "unavailable", "various", "multiple", "unspecified", "desconocido", "vacante"}
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
_MONTHS.update({m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
     "noviembre", "diciembre"], 1)})


def normalize_date(value: Any) -> Optional[str]:
    """Return YYYY, YYYY-MM or YYYY-MM-DD, or None if it cannot be parsed honestly."""
    if value is None:
        return None
    s = str(value).strip()
    if normalize_text(s) in _PLACEHOLDERS or normalize_text(s) in {"present", "incumbent", "current"}:
        return None
    if re.fullmatch(r"\d{4}", s):
        return s
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:[T ].*)?", s)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), m.group(3)
        if not 1 <= mo <= 12:
            return None
        if d is None:
            return f"{y}-{mo:02d}"
        try:
            return datetime(int(y), mo, int(d)).strftime("%Y-%m-%d")
        except ValueError:
            return None
    t = normalize_text(s)
    m = re.fullmatch(r"(\d{1,2}) (?:de )?([a-z]+) (?:de )?(\d{4})", t) or None
    if m and m.group(2) in _MONTHS:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))).strftime("%Y-%m-%d")
        except ValueError:
            return None
    m = re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{4})", t)
    if m and m.group(1) in _MONTHS:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))).strftime("%Y-%m-%d")
        except ValueError:
            return None
    m = re.fullmatch(r"([a-z]+) (?:de )?(\d{4})", t)
    if m and m.group(1) in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1)]:02d}"
    return None


def _clean_urls(v: Any) -> list[str]:
    if not v:
        return []
    out = []
    for u in v if isinstance(v, list) else [v]:
        if isinstance(u, str) and re.match(r"https?://[^\s]+\.[^\s]+", u.strip()):
            out.append(u.strip())
    return list(dict.fromkeys(out))


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)


# ---------------------------------------------------------------------------------------
# Democracy
# ---------------------------------------------------------------------------------------
class IndexAssessmentX(_Base):
    index: str = Field(description="One of: freedom_house, vdem, eiu")
    classification_raw: str = Field(description="Classification label exactly as published, e.g. 'Partly Free' or 'Electoral Autocracy'")
    score: Optional[float] = Field(default=None, description="Numeric score if published (e.g. Freedom House global score out of 100)")
    score_scale: Optional[str] = Field(default=None, description="Scale of the score, e.g. '0-100' or '0-1'")
    assessment_year: Optional[int] = Field(default=None, description="Edition/year of the assessment (e.g. 2025)")
    publication: Optional[str] = Field(default=None, description="Report name, e.g. 'Freedom in the World 2025' or 'V-Dem Democracy Report 2025'")
    evidence_url: Optional[str] = Field(default=None, description="URL of the page or PDF stating this classification, preferably on the index publisher's domain")
    evidence_quote: Optional[str] = Field(default=None, description="Short verbatim quote from the evidence URL containing the classification")

    @field_validator("index")
    @classmethod
    def _index(cls, v: str) -> str:
        t = normalize_text(v).replace(" ", "")
        if "freedom" in t:
            return "freedom_house"
        if "vdem" in t or "varieties" in t:
            return "vdem"
        if "eiu" in t or "economist" in t:
            return "eiu"
        raise ValueError(f"unsupported democracy index: {v}")

    @field_validator("evidence_url")
    @classmethod
    def _url(cls, v):
        urls = _clean_urls(v)
        return urls[0] if urls else None

    @field_validator("assessment_year")
    @classmethod
    def _year(cls, v):
        return v if v is None or 1970 <= v <= 2100 else None


class DemocracyX(_Base):
    assessments: list[IndexAssessmentX] = Field(max_length=6)


_LABELS = {
    "freedom_house": [("not free", "not_free"), ("partly free", "partly_free"), ("free", "free")],
    "vdem": [("liberal democracy", "liberal_democracy"), ("electoral democracy", "electoral_democracy"),
             ("electoral autocracy", "electoral_autocracy"), ("closed autocracy", "closed_autocracy")],
    "eiu": [("full democracy", "full_democracy"), ("flawed democracy", "flawed_democracy"),
            ("hybrid regime", "hybrid_regime"), ("authoritarian", "authoritarian")],
}


def normalize_classification(index: str, raw: str) -> tuple[Optional[str], bool]:
    """Map a published label to a canonical one. Returns (label, grey_zone)."""
    t = normalize_text(raw)
    grey = "upper bound" in t or "lower bound" in t or "grey zone" in t or "gray zone" in t
    for needle, label in _LABELS.get(index, []):
        if needle in t:
            return label, grey
    return None, grey


def classification_phrases(index: str, label: str) -> list[str]:
    """Phrases that must appear in a source for it to support `label`."""
    return [needle for needle, lab in _LABELS.get(index, []) if lab == label]


# ---------------------------------------------------------------------------------------
# Contact legality
# ---------------------------------------------------------------------------------------
class ContactDimensionX(_Base):
    purpose: Purpose
    channel: Channel
    sender: Sender
    recipient: Recipient
    status: ContactStatus = Field(description="allowed, restricted (allowed with conditions), prohibited, or unknown if not established by sources")
    conditions: Optional[str] = Field(default=None, description="Conditions or limits (registration, consent, blackout periods, etc.)")
    legal_basis: Optional[str] = Field(default=None, description="Article/statute establishing the rule, e.g. 'Constitution Art. 8'")
    evidence_url: Optional[str] = None

    @field_validator("evidence_url")
    @classmethod
    def _url(cls, v):
        urls = _clean_urls(v)
        return urls[0] if urls else None


class LawRefX(_Base):
    name: str
    category: LawCategory
    relevance: Optional[str] = None
    url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        urls = _clean_urls(v)
        return urls[0] if urls else None


class ContactX(_Base):
    dimensions: list[ContactDimensionX] = Field(max_length=24)
    laws: list[LawRefX] = Field(default_factory=list, max_length=15)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------------------
# Government structure & officeholders
# ---------------------------------------------------------------------------------------
class OfficeholderX(_Base):
    full_name: str = Field(description="Full name of the person currently holding the office, as on official sources")
    party_name: Optional[str] = Field(default=None, description="Party the officeholder belongs to; null if independent/non-partisan or unknown")
    party_acronym: Optional[str] = None
    coalition: Optional[str] = Field(default=None, description="Electoral coalition, if elected on a coalition ticket")
    term_start: Optional[str] = Field(default=None, description="YYYY-MM-DD (or YYYY) when the current term began")
    term_end: Optional[str] = Field(default=None, description="YYYY-MM-DD (or YYYY) when the term is scheduled to end")
    is_acting: Optional[bool] = Field(default=None, description="True if interim/acting/substitute")
    source_urls: list[str] = Field(default_factory=list, max_length=5, description="URLs that state this person holds this office")

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", (v or "")).strip()
        if normalize_text(v) in _PLACEHOLDERS or len(normalize_text(v).split()) < 2:
            raise ValueError(f"not a usable person name: {v!r}")
        return v

    @field_validator("party_name", "party_acronym", "coalition")
    @classmethod
    def _opt(cls, v):
        if v is None or normalize_text(str(v)) in _PLACEHOLDERS:
            return None
        return str(v).strip()

    @field_validator("term_start", "term_end", mode="before")
    @classmethod
    def _date(cls, v):
        return normalize_date(v)

    @field_validator("source_urls", mode="before")
    @classmethod
    def _urls(cls, v):
        return _clean_urls(v)


def _drop_bad_holders(v: Any) -> Any:
    """Drop holders whose name fails validation instead of failing the whole extraction."""
    if not isinstance(v, list):
        return v
    out = []
    for item in v:
        try:
            OfficeholderX.model_validate(item)
            out.append(item)
        except Exception:
            continue
    return out


class OfficeX(_Base):
    title_en: str = Field(description="English title, e.g. 'President of Mexico', 'Senator'")
    title_local: Optional[str] = Field(default=None, description="Official title in the local language")
    selection_method: SelectionMethod = Field(description="How the OFFICE is filled: direct_election, indirect_election, appointment, mixed, hereditary, ex_officio, unknown")
    selection_details: Optional[str] = Field(default=None, description="e.g. 'plurality, single round'; 'nominated by President, ratified by Senate'")
    seats: Optional[int] = Field(default=None, description="Number of seats/positions of this office")
    term_length_years: Optional[float] = None
    keywords: list[str] = Field(default_factory=list, max_length=6, description="Words a source would use for this office in local language and English, e.g. ['presidenta', 'president']")
    current_holders: list[OfficeholderX] = Field(default_factory=list, max_length=3, description="Current holder(s) — only for single-seat or leadership offices")
    source_urls: list[str] = Field(default_factory=list, max_length=5)

    v_holders = field_validator("current_holders", mode="before")(classmethod(lambda cls, v: _drop_bad_holders(v)))
    v_urls = field_validator("source_urls", mode="before")(classmethod(lambda cls, v: _clean_urls(v)))

    @field_validator("seats")
    @classmethod
    def _seats(cls, v):
        return v if v is None or 0 < v < 100000 else None

    @field_validator("selection_method", mode="before")
    @classmethod
    def _method(cls, v):
        try:
            return SelectionMethod(str(v).strip().lower())
        except ValueError:
            return SelectionMethod.unknown


class BodyX(_Base):
    name_en: str
    name_local: Optional[str] = None
    branch: Branch
    description: Optional[str] = None
    offices: list[OfficeX] = Field(max_length=8)
    source_urls: list[str] = Field(default_factory=list, max_length=5)

    v_urls = field_validator("source_urls", mode="before")(classmethod(lambda cls, v: _clean_urls(v)))

    @field_validator("branch", mode="before")
    @classmethod
    def _branch(cls, v):
        try:
            return Branch(str(v).strip().lower())
        except ValueError:
            return Branch.other


class SubnationalLevelX(_Base):
    name_en: str = Field(description="e.g. 'States (federal entities)', 'Municipalities'")
    name_local: Optional[str] = None
    tier: str = Field(description="'state' for first-level divisions, 'municipal' for local government")
    count: Optional[int] = None
    description: Optional[str] = None


class NationalStructureX(_Base):
    system_of_government: str = Field(description="e.g. 'Federal presidential republic'")
    constitution_name: Optional[str] = None
    constitution_url: Optional[str] = None
    bodies: list[BodyX] = Field(max_length=10)
    subnational_levels: list[SubnationalLevelX] = Field(default_factory=list, max_length=4)
    source_urls: list[str] = Field(default_factory=list, max_length=8)

    v_urls = field_validator("source_urls", mode="before")(classmethod(lambda cls, v: _clean_urls(v)))


class DivisionX(_Base):
    name: str = Field(description="Name of the first-level division (state/province/federal entity)")
    division_type: Optional[str] = Field(default=None, description="e.g. 'state', 'federal entity / capital'")
    executive_title_en: str = Field(description="e.g. 'Governor', or 'Head of Government' for a capital")
    executive_title_local: Optional[str] = None
    executive_selection_method: SelectionMethod
    executive_term_years: Optional[float] = None
    officeholder: Optional[OfficeholderX] = None
    legislature_name_en: Optional[str] = None
    legislature_name_local: Optional[str] = None
    legislature_seats: Optional[int] = None
    legislature_selection_method: Optional[SelectionMethod] = None
    special_status_note: Optional[str] = Field(default=None, description="How this division's governance differs from the others, if it does")

    @field_validator("officeholder", mode="before")
    @classmethod
    def _holder(cls, v):
        kept = _drop_bad_holders([v]) if v else []
        return kept[0] if kept else None

    @field_validator("executive_selection_method", "legislature_selection_method", mode="before")
    @classmethod
    def _method(cls, v):
        if v is None:
            return None
        try:
            return SelectionMethod(str(v).strip().lower())
        except ValueError:
            return SelectionMethod.unknown


class DivisionsX(_Base):
    divisions: list[DivisionX] = Field(max_length=80)


class LocalUnitX(_Base):
    name: str = Field(description="Name of the municipality / borough / local unit")
    parent_division: Optional[str] = Field(default=None, description="State/division it belongs to")
    head_title_en: str = Field(description="e.g. 'Municipal President', 'Mayor of the borough'")
    head_title_local: Optional[str] = None
    head_selection_method: SelectionMethod
    officeholder: Optional[OfficeholderX] = None

    @field_validator("officeholder", mode="before")
    @classmethod
    def _holder(cls, v):
        kept = _drop_bad_holders([v]) if v else []
        return kept[0] if kept else None

    @field_validator("head_selection_method", mode="before")
    @classmethod
    def _method(cls, v):
        try:
            return SelectionMethod(str(v).strip().lower())
        except ValueError:
            return SelectionMethod.unknown


class LocalStructureX(_Base):
    unit_name_en: str = Field(description="Generic name of local units, e.g. 'Municipality'")
    unit_name_local: Optional[str] = None
    total_units: Optional[int] = None
    council_name_en: Optional[str] = Field(default=None, description="Governing body of a unit, e.g. 'Municipal council (Ayuntamiento)'")
    council_name_local: Optional[str] = None
    offices: list[OfficeX] = Field(default_factory=list, max_length=6, description="Generic offices of every unit (no holders)")
    units: list[LocalUnitX] = Field(default_factory=list, max_length=20)
    source_urls: list[str] = Field(default_factory=list, max_length=6)

    v_urls = field_validator("source_urls", mode="before")(classmethod(lambda cls, v: _clean_urls(v)))


class SpecialJurisdictionX(_Base):
    name: str = Field(description="e.g. 'Mexico City'")
    why_distinct: str = Field(description="How its governance differs from ordinary divisions")
    subunit_name_en: str = Field(description="e.g. 'Borough (alcaldía)'")
    subunit_name_local: Optional[str] = None
    subunit_council_name_en: Optional[str] = None
    subunit_council_name_local: Optional[str] = None
    subunits: list[LocalUnitX] = Field(default_factory=list, max_length=40)
    source_urls: list[str] = Field(default_factory=list, max_length=6)

    v_urls = field_validator("source_urls", mode="before")(classmethod(lambda cls, v: _clean_urls(v)))


class SpecialJurisdictionsX(_Base):
    jurisdictions: list[SpecialJurisdictionX] = Field(max_length=4)


# ---------------------------------------------------------------------------------------
# JSON-schema export for Exa Agent outputSchema
# ---------------------------------------------------------------------------------------
_DROP_KEYS = {"title", "default"}


def _inline(node: Any, defs: dict) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            target = defs[node["$ref"].split("/")[-1]]
            merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline(merged, defs)
        out = {}
        for k, v in node.items():
            if k in _DROP_KEYS or k == "$defs":
                continue
            out[k] = _inline(v, defs)
        # Collapse Optional[X] (anyOf [X, null]) to a nullable type when X is a simple schema.
        if "anyOf" in out and len(out["anyOf"]) == 2 and {"type": "null"} in out["anyOf"]:
            other = next(s for s in out["anyOf"] if s != {"type": "null"})
            if isinstance(other.get("type"), str):
                del out["anyOf"]
                merged = {**other, **out}
                merged["type"] = [other["type"], "null"]
                if "enum" in merged and None not in merged["enum"]:
                    merged["enum"] = [*merged["enum"], None]
                out = merged
        return out
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def agent_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema(mode="validation")
    defs = raw.get("$defs", {})
    schema = _inline(raw, defs)
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    return schema


# ---------------------------------------------------------------------------------------
# Legislatures (individual members)
# ---------------------------------------------------------------------------------------
class SeatType(str, Enum):
    general = "general"                      # constituency seat, normally direct election
    women_reserved = "women_reserved"        # e.g. party-list reserved seats for women
    minority_reserved = "minority_reserved"  # e.g. reserved seats for non-Muslims / minorities
    other_reserved = "other_reserved"        # technocrats, ulema, etc.
    proportional_list = "proportional_list"  # list seats in mixed systems


class AssemblyStructureX(_Base):
    total_seats: Optional[int] = None
    general_seats: Optional[int] = Field(default=None, description="Seats filled from single-member constituencies")
    general_seat_selection_method: SelectionMethod = Field(description="How general/constituency seats are filled")
    constituency_prefix: Optional[str] = Field(default=None, description="Code prefix of constituencies, e.g. 'PP' for Punjab Assembly, 'PS' for Sindh; null if constituencies are not coded")
    constituency_first: Optional[int] = Field(default=None, description="Lowest constituency number, e.g. 1")
    constituency_last: Optional[int] = Field(default=None, description="Highest constituency number, e.g. 297")
    reserved_women: Optional[int] = None
    reserved_minorities: Optional[int] = None
    reserved_other: Optional[int] = None
    reserved_selection_method: Optional[SelectionMethod] = Field(default=None, description="How reserved seats are filled (e.g. indirect_election via party lists)")
    last_general_election: Optional[str] = None
    official_members_url: Optional[str] = Field(default=None, description="Official page listing current members")
    source_urls: list[str] = Field(default_factory=list, max_length=6)

    v_urls = field_validator("source_urls", mode="before")(classmethod(lambda cls, v: _clean_urls(v)))

    @field_validator("general_seat_selection_method", "reserved_selection_method", mode="before")
    @classmethod
    def _method(cls, v):
        if v is None:
            return None
        try:
            return SelectionMethod(str(v).strip().lower())
        except ValueError:
            return SelectionMethod.unknown

    @field_validator("last_general_election", mode="before")
    @classmethod
    def _date(cls, v):
        return normalize_date(v)

    @field_validator("official_members_url")
    @classmethod
    def _url(cls, v):
        urls = _clean_urls(v)
        return urls[0] if urls else None


class LegislatorX(OfficeholderX):
    constituency_code: Optional[str] = Field(default=None, description="e.g. 'PP-12'; null for reserved seats")
    constituency_name: Optional[str] = Field(default=None, description="Place name of the constituency, e.g. 'Rawalpindi-VI'")
    seat_type: SeatType = SeatType.general
    by_election: Optional[bool] = Field(default=None, description="True if the member won the seat in a by-election")

    @field_validator("seat_type", mode="before")
    @classmethod
    def _seat(cls, v):
        try:
            return SeatType(str(v).strip().lower())
        except ValueError:
            return SeatType.other_reserved

    @field_validator("constituency_code", mode="before")
    @classmethod
    def _code(cls, v):
        if v is None or normalize_text(str(v)) in _PLACEHOLDERS:
            return None
        return re.sub(r"\s*[-–]\s*", "-", str(v).strip().upper())


class LegislatorsX(_Base):
    members: list[LegislatorX] = Field(max_length=80)

    @field_validator("members", mode="before")
    @classmethod
    def _members(cls, v):
        if not isinstance(v, list):
            return v
        out = []
        for item in v:
            try:
                LegislatorX.model_validate(item)
                out.append(item)
            except Exception:
                continue
        return out
