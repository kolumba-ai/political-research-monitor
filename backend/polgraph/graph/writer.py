"""High-level, idempotent graph writes with provenance."""
from __future__ import annotations

from typing import Iterable

from .. import ids
from ..evidence import DISPLAY_AS_VERIFIED, EvidenceStatus, SupportingSource, Verification, authority_tier
from ..ids import clean_display_name, registrable_domain, utcnow
from ..schemas import OfficeholderX
from .dedupe import find_office, find_person, party_key, same_person
from .store import GraphStore


class GraphWriter:
    def __init__(self, store: GraphStore, mode: str, run_ref: str):
        self.store = store
        self.mode = mode          # "production" | "research_only"
        self.run_ref = run_ref    # pipeline run id for provenance

    # ----------------------------------------------------------------- basics
    def node(self, node_id: str, label: str, **props) -> str:
        self.store.upsert_node(node_id, label, props)
        return node_id

    def edge(self, src: str, etype: str, dst: str, **props) -> None:
        self.store.upsert_edge(src, etype, dst, props)

    def source(self, url: str, title: str | None = None, published_date: str | None = None,
               via: str | None = None) -> str:
        sid = ids.source_id(url)
        self.node(sid, "Source", name=title or registrable_domain(url), url=url, domain=registrable_domain(url),
                  authority_tier=authority_tier(url), published_date=published_date, retrieved_at=utcnow(),
                  retrieved_via=via)
        return sid

    def support(self, node_id: str, sources: Iterable[SupportingSource | str], via: str | None = None,
                claim: str | None = None) -> int:
        n = 0
        for s in sources:
            if isinstance(s, str):
                sid = self.source(s, via=via)
                self.edge(node_id, "SUPPORTED_BY", sid, via=via, claim=claim, checked_at=utcnow())
            else:
                sid = self.source(s.url, s.title, s.published_date, s.via)
                self.edge(node_id, "SUPPORTED_BY", sid, via=s.via, claim=claim, snippet=s.snippet,
                          checked_at=utcnow())
            n += 1
        return n

    def provenance(self, status: str, confidence: float | None = None) -> dict:
        return {"evidence_status": status, "confidence": confidence, "last_verified": utcnow(),
                "pipeline_mode": self.mode, "pipeline_run": self.run_ref}

    # --------------------------------------------------------------- entities
    def office(self, body_node_id: str, *, title_en: str, title_local: str | None, selection_method: str,
               is_elected: bool | None, **props) -> str:
        existing = find_office(self.store, body_node_id, title_en, title_local, seat_type=props.get("seat_type"))
        oid = existing["id"] if existing else ids.office_id(body_node_id, title_en)
        prior = (existing.get("aliases") or []) if existing else []
        canonical = existing["name"] if existing else title_en
        aliases = sorted({*prior, title_en} - {canonical})
        self.node(oid, "PoliticalOffice", name=existing["name"] if existing else title_en, title_local=title_local,
                  selection_method=selection_method, is_elected=is_elected, aliases=aliases or None, **props)
        self.edge(body_node_id, "HAS_OFFICE", oid)
        return oid

    def party(self, iso2: str, name: str | None, acronym: str | None) -> str | None:
        key = party_key(name, acronym)
        if not key:
            return None
        pid = ids.party_id(iso2, key)
        existing = self.store.get_node(pid)
        aliases = set(existing.get("aliases") or []) if existing else set()
        for a in (name, acronym):
            if a:
                aliases.add(a)
        display = (existing or {}).get("name") or name or acronym
        self.node(pid, "PoliticalParty", name=display, acronym=acronym or (existing or {}).get("acronym"),
                  country=iso2.upper(), aliases=sorted(aliases))
        return pid

    def _holds_other_seat(self, person_id: str, office_node_id: str) -> bool:
        """True if the person currently holds a seat in a multi-seat office other than this one.
        Nobody holds two legislative seats at once, so a second seat means a namesake."""
        for e, o in self.store.edges(person_id, "HOLDS_OFFICE", "out"):
            if o["id"] != office_node_id and e.get("current") and o.get("single_seat") is False:
                return True
        return False

    def person(self, iso2: str, full_name: str, seat_office: str | None = None, seat_key: str | None = None) -> str:
        """Resolve or create a Person. `seat_office` (for multi-seat offices) prevents merging with a
        same-named person who already holds a different seat; `seat_key` disambiguates the new id."""
        full_name = clean_display_name(full_name)
        existing = find_person(self.store, iso2, full_name)
        if existing and seat_office and self._holds_other_seat(existing["id"], seat_office):
            pid = f"{ids.person_id(iso2, full_name)}-{ids.short_hash(seat_office + '|' + (seat_key or ''), 6)}"
            self.node(pid, "Person", name=full_name, country=iso2.upper(), aliases=[],
                      namesake_of=existing["id"])
            return pid
        if existing:
            aliases = set(existing.get("aliases") or [])
            # Keep the longest form as the display name (usually includes both surnames).
            name = clean_display_name(existing["name"])
            if len(full_name) > len(name):
                aliases.add(name)
                name = full_name
            elif full_name != name:
                aliases.add(full_name)
            self.node(existing["id"], "Person", name=name, aliases=sorted(aliases))
            return existing["id"]
        pid = ids.person_id(iso2, full_name)
        self.node(pid, "Person", name=full_name, country=iso2.upper(), aliases=[])
        return pid

    # --------------------------------------------------------------- holdings
    def holding(self, iso2: str, office_node_id: str, claim: OfficeholderX, ver: Verification,
                single_seat: bool, extra: dict | None = None) -> dict:
        """Persist a verified officeholder claim. Returns a summary dict. Unsupported claims must be
        filtered out by the caller (they go to the unresolved-claims log, not the graph)."""
        if ver.status == EvidenceStatus.unsupported:
            raise ValueError("unsupported claims are not persisted")
        seat_key = (extra or {}).get("constituency_code") or claim.full_name
        pid = self.person(iso2, claim.full_name, seat_office=None if single_seat else office_node_id,
                          seat_key=seat_key)
        tid = ids.term_id(office_node_id, pid, claim.term_start)
        prov = {**self.provenance(ver.status.value, ver.confidence), **(extra or {})}
        self.node(tid, "Term", name=f"{claim.full_name} — term from {claim.term_start or '?'}",
                  start=claim.term_start, end=claim.term_end, is_acting=claim.is_acting, current=True,
                  office=office_node_id, person=pid, **prov)
        self.edge(office_node_id, "HAS_TERM", tid)
        self.edge(tid, "TERM_HELD_BY", pid)
        self.support(tid, ver.supporting, claim=f"{claim.full_name} holds {office_node_id}")

        demoted = []
        if single_seat and ver.status in DISPLAY_AS_VERIFIED:
            for eprops, other in self.store.edges(office_node_id, "HOLDS_OFFICE", "in"):
                if other["id"] != pid and eprops.get("current") and not same_person(other["name"], claim.full_name):
                    self.edge(other["id"], "HOLDS_OFFICE", office_node_id, current=False, status="former",
                              superseded_at=utcnow(), superseded_by=pid)
                    demoted.append(other["id"])
        self.edge(pid, "HOLDS_OFFICE", office_node_id, current=True, status="current", term_id=tid,
                  start=claim.term_start, end=claim.term_end, is_acting=claim.is_acting, **prov)
        party_id = self.party(iso2, claim.party_name, claim.party_acronym)
        if party_id:
            self.edge(pid, "MEMBER_OF_PARTY", party_id, coalition=claim.coalition, as_of=utcnow(),
                      basis="as reported alongside office-holding claim", term_id=tid)
        self.node(pid, "Person", last_verified=prov["last_verified"])
        return {"person": pid, "term": tid, "party": party_id, "status": ver.status.value, "demoted": demoted}
