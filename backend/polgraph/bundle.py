"""Politician input bundle: the System A -> System B contract.

One bundle per politician holds the country, the politician, and the subgraph between them:

  Country -HAS_GOVERNMENT_LEVEL-> GovernmentLevel [-CONTAINS-> GovernmentLevel ...] -HAS_BODY-> GovernmentBody
    -HAS_OFFICE-> PoliticalOffice -HAS_TERM-> Term -TERM_HELD_BY-> Person
  Person -HOLDS_OFFICE-> PoliticalOffice, Person -MEMBER_OF_PARTY-> PoliticalParty (leaf),
  PoliticalOffice / Term -SUPPORTED_BY-> Source (evidence leaves)

`sha256` covers `country`, `politician`, `nodes` and `edges` only, so re-exporting an unchanged slice gives the
same hash. Works on anything with the read interface `get_node`, `edges(node_id, etype, direction)`.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .ids import utcnow

BUNDLE_FORMAT = 1
_UP = (("HAS_OFFICE", "GovernmentBody"), ("HAS_BODY", "GovernmentLevel"), ("CONTAINS", "GovernmentLevel"),
       ("HAS_GOVERNMENT_LEVEL", "Country"))


class JsonlGraph:
    """In-memory, read-only graph over `knowledge_base/graph/{nodes,edges}.jsonl` or a bundle's nodes/edges."""

    def __init__(self, kb_dir: Path | str | None = None, *, nodes: list[dict] | None = None,
                 edges: list[dict] | None = None):
        self.manifest: dict = {}
        self._nodes: dict[str, dict] = {}
        self._by_label: dict[str, list[dict]] = defaultdict(list)
        self._out: dict[tuple[str, str], list[tuple[dict, str]]] = defaultdict(list)
        self._in: dict[tuple[str, str], list[tuple[dict, str]]] = defaultdict(list)
        if kb_dir is None:
            for n in nodes or []:
                self._add_node(n)
            for e in edges or []:
                self._add_edge(e)
            return
        kb = Path(kb_dir)
        self.manifest = json.loads((kb / "manifest.json").read_text()) if (kb / "manifest.json").exists() else {}
        with open(kb / "graph" / "nodes.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    self._add_node(json.loads(line))
        with open(kb / "graph" / "edges.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    self._add_edge(json.loads(line))

    @classmethod
    def from_bundle(cls, bundle: dict) -> "JsonlGraph":
        return cls(nodes=bundle["nodes"], edges=bundle["edges"])

    def _add_node(self, n: dict) -> None:
        self._nodes[n["id"]] = n
        self._by_label[n["label"]].append(n)

    def _add_edge(self, e: dict) -> None:
        props = {k: v for k, v in e.items() if k not in ("src", "dst")}
        self._out[(e["src"], e["type"])].append((props, e["dst"]))
        self._in[(e["dst"], e["type"])].append((props, e["src"]))

    def get_node(self, node_id: str) -> dict | None:
        return self._nodes.get(node_id)

    def edges(self, node_id: str, etype: str, direction: str) -> list[tuple[dict, dict]]:
        idx = self._out if direction == "out" else self._in
        out = [(p, self._nodes[o]) for p, o in idx.get((node_id, etype), []) if o in self._nodes]
        return sorted(out, key=lambda t: t[1]["id"])

    def nodes_by_label(self, label: str) -> list[dict]:
        return sorted(self._by_label.get(label, []), key=lambda n: n["id"])


def bundle_sha256(bundle: dict) -> str:
    core = {k: bundle[k] for k in ("format", "country", "politician", "nodes", "edges")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def build_bundle(g, country_id: str, person_id: str) -> dict | None:
    """None if either node is missing. Offices whose chain does not reach `country_id` are listed in
    `unreached_offices`; the consumer decides whether that is acceptable."""
    person, country = g.get_node(person_id), g.get_node(country_id)
    if not person or person.get("label") != "Person" or not country or country.get("label") != "Country":
        return None
    nodes: dict[str, dict] = {person_id: person, country_id: country}
    edges: dict[tuple, dict] = {}

    def add(src: str, props: dict, dst: str, node: dict) -> None:
        nodes[node["id"]] = node
        etype = props["type"]
        edges[(src, etype, dst)] = {**{k: v for k, v in props.items() if k != "type"}, "src": src, "type": etype,
                                    "dst": dst}

    def sources(nid: str) -> None:
        for e, src in g.edges(nid, "SUPPORTED_BY", "out"):
            add(nid, {**e, "type": "SUPPORTED_BY"}, src["id"], src)

    offices: list[str] = []
    for e, office in g.edges(person_id, "HOLDS_OFFICE", "out"):
        add(person_id, {**e, "type": "HOLDS_OFFICE"}, office["id"], office)
        offices.append(office["id"])
    for e, term in g.edges(person_id, "TERM_HELD_BY", "in"):
        add(term["id"], {**e, "type": "TERM_HELD_BY"}, person_id, term)
        sources(term["id"])
        for oe, office in g.edges(term["id"], "HAS_TERM", "in"):
            add(office["id"], {**oe, "type": "HAS_TERM"}, term["id"], office)
            offices.append(office["id"])
    for e, party in g.edges(person_id, "MEMBER_OF_PARTY", "out"):
        add(person_id, {**e, "type": "MEMBER_OF_PARTY"}, party["id"], party)

    paths: dict[str, list[str]] = {}
    unreached: list[str] = []
    for oid in sorted(set(offices)):
        sources(oid)
        chain, cur = [oid], oid
        while cur != country_id and len(chain) < 20:
            step = next(((et, n) for et, label in _UP for e, n in g.edges(cur, et, "in")
                         if n.get("label") == label and n["id"] not in chain), None)
            if step is None:
                break
            et, parent = step
            add(parent["id"], {"type": et}, cur, parent)
            chain.append(parent["id"])
            cur = parent["id"]
        if cur == country_id:
            paths[oid] = list(reversed(chain))
        else:
            unreached.append(oid)

    bundle = {"format": BUNDLE_FORMAT, "country": country_id, "politician": person_id,
              "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
              "edges": sorted(edges.values(), key=lambda e: (e["src"], e["type"], e["dst"])),
              "paths": paths, "unreached_offices": unreached}
    bundle["sha256"] = bundle_sha256(bundle)
    return bundle


def bundle_filename(person_id: str) -> str:
    return person_id.replace(":", "__") + ".json"


def export_bundles(g, out: Path, countries: list[str]) -> dict:
    """Writes `out/<ISO2>/<person>.json` for every Person of each country, plus `out/<ISO2>/index.json`."""
    summary = {}
    for iso2 in (c.upper() for c in countries):
        cdir = out / iso2
        cdir.mkdir(parents=True, exist_ok=True)
        index = []
        for p in g.nodes_by_label("Person"):
            if (p.get("country") or "").upper() != iso2:
                continue
            b = build_bundle(g, f"country:{iso2}", p["id"])
            if b is None:
                continue
            (cdir / bundle_filename(p["id"])).write_text(json.dumps(b, ensure_ascii=False, indent=1, sort_keys=True))
            index.append({"politician": p["id"], "name": p.get("name"), "aliases": p.get("aliases") or [],
                          "file": bundle_filename(p["id"]), "sha256": b["sha256"]})
        (cdir / "index.json").write_text(json.dumps({"format": BUNDLE_FORMAT, "country": f"country:{iso2}",
                                                     "exported_at": utcnow(), "bundles": index},
                                                    ensure_ascii=False, indent=1))
        keep = {e["file"] for e in index}
        for stale in cdir.glob("person__*.json"):
            if stale.name not in keep:
                stale.unlink()
        summary[iso2] = len(index)
    return summary
