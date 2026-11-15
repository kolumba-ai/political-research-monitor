"""Repair Person nodes that an earlier, looser name-matching rule merged incorrectly.

Every office-holding keeps the exact name that was claimed for it on its Term node
("<claimed name> — term from <date>"). This tool regroups each Person's holdings by those claimed names
with the current `same_person` rule; holdings that belong to someone else are moved (HOLDS_OFFICE,
TERM_HELD_BY and the term's MEMBER_OF_PARTY edge) to the correct Person, and each Person's display name
and aliases are rebuilt from its own holdings. Idempotent: running it on a clean graph changes nothing.
"""
from __future__ import annotations

from . import ids
from .graph.dedupe import same_person
from .graph.store import GraphStore
from .ids import clean_display_name, utcnow
from .logging_setup import get_logger

log = get_logger("repair")


def _claimed(term: dict) -> str:
    return clean_display_name((term.get("name") or "").split(" — term from")[0].strip())


def _clusters(names: dict[str, str]) -> list[list[str]]:
    """Union-find over term ids whose claimed names are the same person."""
    parent = {k: k for k in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    keys = list(names)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if same_person(names[a], names[b]):
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


def _split_seats(store: GraphStore, group: list[str], terms: dict[str, dict],
                 holds: dict | None = None) -> list[list[str]]:
    """One person holds at most one seat in a multi-seat office (legislature) at a time; extra seat
    holdings in the same name-cluster are namesakes and become their own groups."""
    keep, extra = [], []
    seat_office = None
    for tid in sorted(group, key=lambda t: terms[t].get("first_seen") or ""):
        office = store.get_node(terms[tid].get("office") or "") or {}
        # "current" comes from the live HOLDS_OFFICE edge when there is one (reconciliation updates it).
        edge = (holds or {}).get(tid, (None, None))[0]
        current = edge.get("current") if edge is not None else terms[tid].get("current", True)
        if office.get("single_seat") is False and current:
            # Several terms on the SAME office are one seat (duplicate records), not a second seat.
            if seat_office is not None and office.get("id") != seat_office:
                extra.append([tid])
                continue
            seat_office = office.get("id")
        keep.append(tid)
    return [keep] + extra if keep else extra


def _strip(props: dict) -> dict:
    return {k: v for k, v in props.items() if k not in ("type", "first_seen", "updated_at")}


def repair_people(store: GraphStore, country: str | None = None) -> dict:
    stats = {"persons_checked": 0, "persons_split": 0, "holdings_moved": 0, "names_fixed": 0}
    persons = [p for p in store.nodes_by_label("Person") if not country or p.get("country") == country.upper()]
    for p in persons:
        stats["persons_checked"] += 1
        terms = {t["id"]: t for _, t in store.edges(p["id"], "TERM_HELD_BY", "in")}
        if not terms:
            continue
        names = {tid: _claimed(t) for tid, t in terms.items() if _claimed(t)}
        if not names:
            continue
        holds = {e.get("term_id"): (e, o) for e, o in store.edges(p["id"], "HOLDS_OFFICE", "out")}
        parties = [(e, party) for e, party in store.edges(p["id"], "MEMBER_OF_PARTY", "out")]
        iso = p.get("country") or "XX"

        def owns_id(g):   # the cluster whose name produced this Person's canonical id stays on it
            return any(ids.person_id(iso, names[t]) == p["id"] for t in g)
        groups = []
        for g in _clusters(names):
            groups.extend(_split_seats(store, g, terms, holds))
        groups = sorted(groups, key=lambda g: (not owns_id(g),
                                                         min(terms[t].get("first_seen") or "" for t in g)))
        keep, move = groups[0], groups[1:]

        for g in move:
            stats["persons_split"] += 1
            new_name = max((names[t] for t in g), key=len)
            iso2 = p.get("country") or "XX"
            existing = next((c for c in store.search(new_name.split()[-1], ["Person"], 50)
                             if c["id"] != p["id"] and c.get("country") == iso2 and same_person(c["name"], new_name)),
                            None)
            seat_offices = {terms[t].get("office") for t in g}
            if existing and any(
                    o["id"] not in seat_offices and e.get("current") and o.get("single_seat") is False
                    for e, o in store.edges(existing["id"], "HOLDS_OFFICE", "out")):
                existing = None   # same name but already seated elsewhere: a namesake
            nid = existing["id"] if existing else ids.person_id(iso2, new_name)
            if nid == p["id"] or (not existing and store.get_node(nid)):
                nid = f"{ids.person_id(iso2, new_name)}-{ids.short_hash('|'.join(sorted(map(str, seat_offices))), 6)}"
            if nid == p["id"]:   # still colliding with itself: disambiguate by the moved terms
                nid = f"{ids.person_id(iso2, new_name)}-{ids.short_hash('|'.join(sorted(g)), 6)}"
            store.upsert_node(nid, "Person", {"name": new_name, "country": iso2,
                                              "aliases": (existing or {}).get("aliases") or [],
                                              "last_verified": utcnow()})
            for tid in g:
                e, office = holds.get(tid, (None, None))
                if e is not None:
                    store.delete_edge(p["id"], "HOLDS_OFFICE", office["id"])
                    store.upsert_edge(nid, "HOLDS_OFFICE", office["id"], _strip(e))
                elif terms[tid].get("office"):
                    # Two people had been merged onto one (person, office) edge: rebuild this holding
                    # from the Term, which carries the original holding's properties.
                    t = terms[tid]
                    props = {k: v for k, v in _strip(t).items() if k not in ("name", "office", "person", "id",
                                                                              "label")}
                    store.upsert_edge(nid, "HOLDS_OFFICE", t["office"],
                                      {**props, "status": "current" if t.get("current") else "former",
                                       "term_id": tid})
                store.delete_edge(tid, "TERM_HELD_BY", p["id"])
                store.upsert_edge(tid, "TERM_HELD_BY", nid, {})
                store.upsert_node(tid, "Term", {"person": nid})
                for pe, party in parties:
                    if pe.get("term_id") == tid:
                        store.delete_edge(p["id"], "MEMBER_OF_PARTY", party["id"])
                        store.upsert_edge(nid, "MEMBER_OF_PARTY", party["id"], _strip(pe))
                stats["holdings_moved"] += 1
            _rebuild_name(store, nid, [names[t] for t in g],
                          [holds[t][0].get("roster_name") for t in g if t in holds])
            log.info("repair.moved", from_person=p["id"], to_person=nid, name=new_name, holdings=len(g))

        old = p["name"]
        new = _rebuild_name(store, p["id"], [names[t] for t in keep],
                            [holds[t][0].get("roster_name") for t in keep if t in holds],
                            keep_aliases=[a for a in (p.get("aliases") or []) if same_person(a, max(
                                (names[t] for t in keep), key=len))])
        if new != old:
            stats["names_fixed"] += 1
    log.info("repair.done", **stats)
    return stats


def _rebuild_name(store: GraphStore, pid: str, claimed: list[str], roster: list[str | None],
                  keep_aliases: list[str] | None = None) -> str:
    name = max(claimed, key=len)
    aliases = ({*claimed, *(r for r in roster if r), *(keep_aliases or [])}) - {name}
    store.upsert_node(pid, "Person", {"name": name, "aliases": sorted(aliases)})
    return name
