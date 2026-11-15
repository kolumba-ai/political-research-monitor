"""Read-only access to System A's knowledge graph and the per-run identity snapshot (System A → B contract).

System B never writes to System A and never creates politician identities: every politician is a `Person` id
read from the graph. The source is either the exported `knowledge_base/` snapshot (JSONL) or System A's live
store (anything with System A's `GraphStore` read interface: `get_node`, `edges`, `nodes_by_label`).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from polgraph.bundle import BUNDLE_FORMAT, JsonlGraph, build_bundle, bundle_filename, bundle_sha256
from polgraph.ids import normalize_text

TRACKABLE_STATUSES = {"verified", "official_single"}
REVIEW_STATUSES = {"single_source"}


class GraphReader(Protocol):
    def get_node(self, node_id: str) -> dict | None: ...
    def edges(self, node_id: str, etype: str, direction: str) -> list[tuple[dict, dict]]: ...
    def nodes_by_label(self, label: str) -> list[dict]: ...


KnowledgeBaseGraph = JsonlGraph


class BundleDir:
    """System A's exported politician input bundles: `<dir>/<ISO2>/index.json` + one file per politician."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._index: dict[str, tuple[Path, dict]] = {}
        for idx in sorted(self.path.glob("*/index.json")):
            data = json.loads(idx.read_text())
            iso2 = data["country"].split(":")[1]
            for entry in data["bundles"]:
                node = {"id": entry["politician"], "label": "Person", "name": entry.get("name"),
                        "aliases": entry.get("aliases") or [], "country": iso2}
                self._index[entry["politician"]] = (idx.parent / entry["file"], node)

    def bundle(self, politician_id: str) -> dict | None:
        hit = self._index.get(politician_id)
        if not hit:
            path = next(iter(self.path.glob(f"*/{bundle_filename(politician_id)}")), None)
            return json.loads(path.read_text()) if path else None
        return json.loads(hit[0].read_text())

    def get_node(self, node_id: str) -> dict | None:
        hit = self._index.get(node_id)
        return hit[1] if hit else None

    def edges(self, node_id: str, etype: str, direction: str) -> list[tuple[dict, dict]]:
        return []

    def nodes_by_label(self, label: str) -> list[dict]:
        return sorted((n for _, n in self._index.values()), key=lambda n: n["id"]) if label == "Person" else []


def get_bundle(g, politician_id: str, country_id: str) -> dict | None:
    """The politician's input bundle: read from exported files, or built from a full graph (tests, live store)."""
    return g.bundle(politician_id) if hasattr(g, "bundle") else build_bundle(g, country_id, politician_id)


def validate_bundle(bundle: dict, country_id: str, politician_id: str) -> tuple[list[str], list[str]]:
    """(errors, warnings). Paths are recomputed from the bundle's own nodes and edges, not trusted from `paths`."""
    errors: list[str] = []
    warnings: list[str] = []
    if bundle.get("format") != BUNDLE_FORMAT:
        errors.append(f"unsupported bundle format {bundle.get('format')!r}")
    if bundle.get("country") != country_id:
        errors.append(f"bundle country {bundle.get('country')!r} != {country_id!r}")
    if bundle.get("politician") != politician_id:
        errors.append(f"bundle politician {bundle.get('politician')!r} != {politician_id!r}")
    if errors:
        return errors, warnings
    if bundle.get("sha256") != bundle_sha256(bundle):
        errors.append("bundle sha256 does not match its content")
    bg = JsonlGraph.from_bundle(bundle)
    rebuilt = build_bundle(bg, country_id, politician_id)
    if rebuilt is None:
        return errors + ["bundle lacks the Country or Person node"], warnings
    person_cc = (bg.get_node(politician_id).get("country") or "").upper()
    if person_cc and f"country:{person_cc}" != country_id:
        errors.append(f"person belongs to country:{person_cc}, not {country_id}")
    for e, office in bg.edges(politician_id, "HOLDS_OFFICE", "out"):
        if e.get("current") and office["id"] in rebuilt["unreached_offices"]:
            errors.append(f"no Country -> ... -> PoliticalOffice path for current office {office['id']}")
    if not bg.edges(politician_id, "MEMBER_OF_PARTY", "out"):
        warnings.append("no MEMBER_OF_PARTY leaf")
    return errors, warnings


def open_reader(settings) -> GraphReader:
    if settings.system_a_source == "bundles":
        return BundleDir(settings.system_a_bundle_dir)
    if settings.system_a_source == "knowledge_base":
        return KnowledgeBaseGraph(settings.system_a_kb_dir)
    from polgraph.config import get_settings as system_a_settings
    from polgraph.graph.store import open_store
    return open_store(system_a_settings())


# ---------------------------------------------------------------------------------------------------------------
@dataclass
class OfficeHolding:
    office_id: str
    name: str | None
    title_local: str | None
    keywords: list[str]
    current: bool
    status: str | None
    start: str | None
    end: str | None
    is_acting: bool | None
    evidence_status: str | None
    confidence: float | None
    term_id: str | None
    pipeline_mode: str | None
    pipeline_run: str | None
    body_id: str | None = None
    body_name: str | None = None
    level_id: str | None = None
    level_name: str | None = None
    level_tier: str | None = None
    iso_3166_2: str | None = None


@dataclass
class PersonSnapshot:
    politician_id: str
    country_code: str
    name: str
    aliases: list[str]
    person_updated_at: str
    last_verified: str | None
    graph_run: str | None
    pipeline_mode: str
    country: dict
    offices: list[OfficeHolding] = field(default_factory=list)
    parties: list[dict] = field(default_factory=list)
    bundle_sha256: str | None = None
    input_errors: list[str] = field(default_factory=list)
    input_warnings: list[str] = field(default_factory=list)

    @property
    def current_offices(self) -> list[OfficeHolding]:
        return [o for o in self.offices if o.current]

    @property
    def evidence_status(self) -> str | None:
        """Strongest evidence status among current holdings."""
        order = ["verified", "official_single", "single_source"]
        found = {o.evidence_status for o in self.current_offices}
        return next((s for s in order if s in found), None)

    @property
    def tracking_status(self) -> str:
        """`invalid_input` (bundle failed validation), `trackable` (verified / official_single current holding),
        `needs_review` (single_source), else `ineligible`."""
        if self.input_errors:
            return "invalid_input"
        status = self.evidence_status
        if status in TRACKABLE_STATUSES:
            return "trackable"
        if status in REVIEW_STATUSES:
            return "needs_review"
        return "ineligible"

    def identity_fields(self) -> dict:
        """Fields whose change creates a new `politician_ref` version (timestamps excluded)."""
        return {
            "politician_id": self.politician_id, "name": self.name, "aliases": sorted(self.aliases),
            "pipeline_mode": self.pipeline_mode,
            "offices": sorted(({k: v for k, v in asdict(o).items() if k not in ("pipeline_run",)}
                               for o in self.offices), key=lambda o: (o["office_id"], o["start"] or "")),
            "parties": sorted(({k: v for k, v in p.items() if k != "as_of"} for p in self.parties),
                              key=lambda p: p["party_id"]),
        }

    def snapshot_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.identity_fields(), sort_keys=True, default=str).encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(evidence_status=self.evidence_status, tracking_status=self.tracking_status)
        return d


def country_pipeline_mode(country: dict) -> str:
    """System A has no `Country.pipeline_mode`; a country is `production` only when it passed both gates and was
    researched in production mode (`research_status == enriched`). Everything else stays `research_only`."""
    if country.get("pipeline_mode") in ("production", "research_only"):
        return country["pipeline_mode"]
    if country.get("eligible") is True and country.get("research_status") == "enriched":
        return "production"
    return "research_only"


def _jurisdiction(g: GraphReader, office_id: str) -> dict:
    out: dict = {}
    bodies = g.edges(office_id, "HAS_OFFICE", "in")
    if not bodies:
        return out
    body = bodies[0][1]
    out.update(body_id=body["id"], body_name=body.get("name"))
    levels = g.edges(body["id"], "HAS_BODY", "in")
    if levels:
        lvl = levels[0][1]
        out.update(level_id=lvl["id"], level_name=lvl.get("name"), level_tier=lvl.get("tier"),
                   iso_3166_2=lvl.get("iso_3166_2") or lvl.get("parent_iso_3166_2"))
    return out


def read_person(g: GraphReader, politician_id: str, country_code: str | None = None) -> PersonSnapshot | None:
    """Builds the snapshot from the politician's input bundle only (country + politician + the subgraph between)."""
    iso2 = (country_code or politician_id.split(":")[1]).upper()
    bundle = get_bundle(g, politician_id, f"country:{iso2}")
    if bundle is None:
        return None
    errors, warnings = validate_bundle(bundle, f"country:{iso2}", politician_id)
    snap = _snapshot(JsonlGraph.from_bundle(bundle), politician_id)
    if snap:
        snap.bundle_sha256, snap.input_errors, snap.input_warnings = bundle.get("sha256"), errors, warnings
        snap.bundle = bundle
    return snap


def _snapshot(g: GraphReader, politician_id: str) -> PersonSnapshot | None:
    person = g.get_node(politician_id)
    if not person or person.get("label") != "Person":
        return None
    iso2 = (person.get("country") or politician_id.split(":")[1]).upper()
    country = g.get_node(f"country:{iso2}") or {}
    country_mode = country_pipeline_mode(country)

    offices: list[OfficeHolding] = []
    for e, office in g.edges(politician_id, "HOLDS_OFFICE", "out"):
        term = g.get_node(e["term_id"]) if e.get("term_id") else None
        offices.append(OfficeHolding(
            office_id=office["id"], name=office.get("name"), title_local=office.get("title_local"),
            keywords=list(office.get("keywords") or []), current=bool(e.get("current")), status=e.get("status"),
            start=e.get("start") or (term or {}).get("start"), end=e.get("end") or (term or {}).get("end"),
            is_acting=e.get("is_acting"), evidence_status=e.get("evidence_status") or (term or {}).get("evidence_status"),
            confidence=e.get("confidence"), term_id=e.get("term_id"),
            pipeline_mode=e.get("pipeline_mode"), pipeline_run=e.get("pipeline_run") or (term or {}).get("pipeline_run"),
            **_jurisdiction(g, office["id"]),
        ))
    parties = [{"party_id": p["id"], "name": p.get("name"), "acronym": p.get("acronym"),
                "aliases": list(p.get("aliases") or []), "coalition": e.get("coalition"), "as_of": e.get("as_of")}
               for e, p in g.edges(politician_id, "MEMBER_OF_PARTY", "out")]

    runs = sorted({o.pipeline_run for o in offices if o.pipeline_run})
    # Any research_only holding (or country) keeps the person research_only.
    modes = {country_mode} | {o.pipeline_mode for o in offices if o.current and o.pipeline_mode}
    mode = "research_only" if "research_only" in modes else "production"
    return PersonSnapshot(
        politician_id=politician_id, country_code=iso2, name=person["name"], aliases=list(person.get("aliases") or []),
        person_updated_at=person.get("updated_at") or person.get("last_verified") or person.get("first_seen"),
        last_verified=person.get("last_verified"), graph_run=runs[-1] if runs else None, pipeline_mode=mode,
        country={"iso2": iso2, "eligible": country.get("eligible"), "research_status": country.get("research_status"),
                 "pipeline_mode": country_mode},
        offices=offices, parties=parties,
    )


def list_persons(g: GraphReader, country_code: str | None = None, include_ineligible: bool = False) -> list[PersonSnapshot]:
    out = []
    for p in g.nodes_by_label("Person"):
        if country_code and (p.get("country") or "").upper() != country_code.upper():
            continue
        snap = read_person(g, p["id"], country_code)
        if snap and (include_ineligible or snap.tracking_status != "ineligible"):
            out.append(snap)
    return out


class AmbiguousName(LookupError):
    pass


def resolve_name(g: GraphReader, name: str, country_code: str | None = None) -> str:
    """A user-supplied name is only a search alias: it must match exactly one Person's name or alias."""
    q = normalize_text(name)
    hits = []
    for p in g.nodes_by_label("Person"):
        if country_code and (p.get("country") or "").upper() != country_code.upper():
            continue
        names = [p.get("name") or ""] + list(p.get("aliases") or [])
        normed = [normalize_text(n) for n in names]
        if q in normed or any(q and set(q.split()) <= set(n.split()) for n in normed):
            hits.append(p["id"])
    if len(hits) != 1:
        raise AmbiguousName(f"{name!r} matched {len(hits)} persons: {hits[:10]}")
    return hits[0]
