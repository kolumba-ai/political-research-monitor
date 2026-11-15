"""Graph storage. `GraphStore` is the interface; Neo4j is the production backend and an embedded
SQLite property-graph implements identical semantics for zero-setup use and tests.

Upsert semantics (both backends):
  * nodes are keyed by canonical `id`; properties are MERGED (None values never erase data);
  * `first_seen` is set once, `updated_at` on every write;
  * edges are keyed by (src, type, dst) and also merge properties.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from ..ids import normalize_text, utcnow
from .model import CHILD_RULES, EDGE_TYPES, NODE_LABELS, PARENT_RULES, SEARCHABLE_LABELS


def _clean(props: dict) -> dict:
    return {k: v for k, v in props.items() if v is not None}


class GraphStore(ABC):
    # ---- writes ----
    @abstractmethod
    def upsert_node(self, node_id: str, label: str, props: dict) -> None: ...

    @abstractmethod
    def upsert_edge(self, src: str, etype: str, dst: str, props: dict | None = None) -> None: ...

    @abstractmethod
    def delete_edge(self, src: str, etype: str, dst: str) -> None: ...

    # ---- reads ----
    @abstractmethod
    def get_node(self, node_id: str) -> dict | None: ...

    @abstractmethod
    def edges(self, node_id: str, etype: str, direction: str) -> list[tuple[dict, dict]]:
        """[(edge_props_with_type, neighbor_node)] for edges of `etype` in `direction` (out|in)."""

    @abstractmethod
    def nodes_by_label(self, label: str) -> list[dict]: ...

    @abstractmethod
    def search(self, query: str, labels: list[str], limit: int = 25) -> list[dict]: ...

    @abstractmethod
    def counts(self) -> dict: ...

    @abstractmethod
    def all_edges(self) -> list[dict]:
        """Every relationship as {src, type, dst, **props}."""

    def close(self) -> None:  # pragma: no cover - optional
        pass

    # ---- shared helpers ----
    @staticmethod
    def _check(label: str | None = None, etype: str | None = None) -> None:
        if label is not None and label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label}")
        if etype is not None and etype not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {etype}")

    def children(self, node_id: str) -> list[dict]:
        node = self.get_node(node_id)
        if not node:
            return []
        out = []
        for etype, direction in CHILD_RULES.get(node["label"], []):
            for eprops, nb in self.edges(node_id, etype, direction):
                out.append({"edge": {**eprops, "type": etype, "direction": direction}, "node": nb})
        return out

    def parent(self, node_id: str) -> dict | None:
        node = self.get_node(node_id)
        if not node:
            return None
        for etype, direction in PARENT_RULES.get(node["label"], []):
            found = self.edges(node_id, etype, direction)
            if found:
                return found[0][1]
        return None

    def path_from_root(self, node_id: str, root: str = "earth") -> list[dict]:
        """Walk parent links up to Earth (bounded), returned root-first."""
        path, cur, seen = [], self.get_node(node_id), set()
        while cur and cur["id"] not in seen and len(path) < 20:
            path.append(cur)
            seen.add(cur["id"])
            if cur["id"] == root:
                break
            cur = self.parent(cur["id"])
        return list(reversed(path))

    def sources_for(self, node_id: str) -> list[dict]:
        return [nb for _, nb in self.edges(node_id, "SUPPORTED_BY", "out")]

    def bfs_ids(self, start: str, max_nodes: int = 5000) -> Iterable[str]:
        q, seen = deque([start]), {start}
        while q and len(seen) < max_nodes:
            nid = q.popleft()
            yield nid
            for ch in self.children(nid):
                cid = ch["node"]["id"]
                if cid not in seen:
                    seen.add(cid)
                    q.append(cid)


# ======================================================================================
class SqliteGraphStore(GraphStore):
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, label TEXT NOT NULL, props TEXT NOT NULL,
                    search TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS nodes_label ON nodes(label);
                CREATE TABLE IF NOT EXISTS edges (src TEXT NOT NULL, type TEXT NOT NULL, dst TEXT NOT NULL,
                    props TEXT NOT NULL, PRIMARY KEY (src, type, dst));
                CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);
                """
            )

    @staticmethod
    def _search_text(props: dict) -> str:
        parts = [props.get("name"), props.get("name_local"), props.get("title_local"), props.get("acronym")]
        parts += props.get("aliases") or []
        return normalize_text(" ".join(str(p) for p in parts if p))

    def upsert_node(self, node_id: str, label: str, props: dict) -> None:
        self._check(label=label)
        now = utcnow()
        with self._lock, self._db:
            row = self._db.execute("SELECT props FROM nodes WHERE id=?", (node_id,)).fetchone()
            merged = json.loads(row[0]) if row else {"first_seen": now}
            merged.update(_clean(props))
            merged["updated_at"] = now
            self._db.execute(
                "INSERT INTO nodes(id,label,props,search) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET label=excluded.label, props=excluded.props, search=excluded.search",
                (node_id, label, json.dumps(merged, default=str), self._search_text(merged)),
            )

    def upsert_edge(self, src: str, etype: str, dst: str, props: dict | None = None) -> None:
        self._check(etype=etype)
        with self._lock, self._db:
            for nid in (src, dst):
                if not self._db.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone():
                    raise KeyError(f"edge endpoint {nid} does not exist")
            row = self._db.execute("SELECT props FROM edges WHERE src=? AND type=? AND dst=?",
                                   (src, etype, dst)).fetchone()
            merged = json.loads(row[0]) if row else {"first_seen": utcnow()}
            merged.update(_clean(props or {}))
            merged["updated_at"] = utcnow()
            self._db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?)",
                             (src, etype, dst, json.dumps(merged, default=str)))

    def delete_edge(self, src: str, etype: str, dst: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM edges WHERE src=? AND type=? AND dst=?", (src, etype, dst))

    def _row(self, row) -> dict:
        return {"id": row[0], "label": row[1], **json.loads(row[2])}

    def get_node(self, node_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT id,label,props FROM nodes WHERE id=?", (node_id,)).fetchone()
        return self._row(row) if row else None

    def edges(self, node_id: str, etype: str, direction: str) -> list[tuple[dict, dict]]:
        col, other = ("src", "dst") if direction == "out" else ("dst", "src")
        with self._lock:
            rows = self._db.execute(
                f"SELECT e.props, n.id, n.label, n.props FROM edges e JOIN nodes n ON n.id = e.{other} "
                f"WHERE e.{col}=? AND e.type=? ORDER BY n.id",
                (node_id, etype),
            ).fetchall()
        return [({**json.loads(r[0]), "type": etype}, self._row(r[1:])) for r in rows]

    def nodes_by_label(self, label: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id,label,props FROM nodes WHERE label=? ORDER BY id", (label,)).fetchall()
        return [self._row(r) for r in rows]

    def search(self, query: str, labels: list[str] | None = None, limit: int = 25) -> list[dict]:
        q = normalize_text(query)
        if not q:
            return []
        labels = labels or SEARCHABLE_LABELS
        ph = ",".join("?" for _ in labels)
        with self._lock:
            rows = self._db.execute(
                f"SELECT id,label,props FROM nodes WHERE label IN ({ph}) AND search LIKE ? LIMIT ?",
                (*labels, f"%{q}%", limit * 4),
            ).fetchall()
        res = [self._row(r) for r in rows]
        res.sort(key=lambda n: (not normalize_text(n.get("name", "")).startswith(q), len(n.get("name", ""))))
        return res[:limit]

    def all_edges(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT src, type, dst, props FROM edges ORDER BY src, type, dst").fetchall()
        return [{"src": r[0], "type": r[1], "dst": r[2], **json.loads(r[3])} for r in rows]

    def counts(self) -> dict:
        with self._lock:
            n = dict(self._db.execute("SELECT label, COUNT(*) FROM nodes GROUP BY label").fetchall())
            e = dict(self._db.execute("SELECT type, COUNT(*) FROM edges GROUP BY type").fetchall())
        return {"nodes": n, "edges": e}

    def close(self) -> None:
        self._db.close()


# ======================================================================================
class Neo4jGraphStore(GraphStore):
    """Neo4j backend. Every node carries the generic `:Node` label (unique id constraint) plus its
    specific label. Nested values are stored as JSON strings because Neo4j properties must be
    primitives or homogeneous arrays."""

    def __init__(self, uri: str, user: str, password: str):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()
        with self.driver.session() as s:
            s.run("CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE")
            s.run("CREATE INDEX node_search IF NOT EXISTS FOR (n:Node) ON (n.search)")
            for label in SEARCHABLE_LABELS:
                s.run(f"CREATE INDEX {label.lower()}_name IF NOT EXISTS FOR (n:{label}) ON (n.name)")

    @staticmethod
    def _encode(props: dict) -> dict:
        out = {}
        for k, v in _clean(props).items():
            if isinstance(v, (dict,)) or (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)):
                out[k] = "json:" + json.dumps(v, default=str)
            elif isinstance(v, list):
                out[k] = [str(x) for x in v]
            else:
                out[k] = v
        return out

    @staticmethod
    def _decode(props: dict) -> dict:
        return {k: (json.loads(v[5:]) if isinstance(v, str) and v.startswith("json:") else v)
                for k, v in props.items() if k != "search"}

    def _node(self, n) -> dict:
        props = self._decode(dict(n))
        label = next((l for l in n.labels if l != "Node"), "Node")
        return {"id": props.pop("id"), "label": label, **props}

    def upsert_node(self, node_id: str, label: str, props: dict) -> None:
        self._check(label=label)
        p = self._encode(props)
        search = SqliteGraphStore._search_text(props)
        with self.driver.session() as s:
            s.run(
                f"MERGE (n:Node {{id:$id}}) ON CREATE SET n.first_seen=$now "
                f"SET n:{label}, n += $props, n.updated_at=$now, "
                f"n.search = CASE WHEN $search = '' THEN n.search ELSE $search END",
                id=node_id, props=p, now=utcnow(), search=search,
            )

    def upsert_edge(self, src: str, etype: str, dst: str, props: dict | None = None) -> None:
        self._check(etype=etype)
        with self.driver.session() as s:
            res = s.run(
                f"MATCH (a:Node {{id:$src}}) MATCH (b:Node {{id:$dst}}) MERGE (a)-[r:{etype}]->(b) "
                f"ON CREATE SET r.first_seen=$now SET r += $props, r.updated_at=$now RETURN count(r) AS c",
                src=src, dst=dst, props=self._encode(props or {}), now=utcnow(),
            ).single()
            if not res or res["c"] == 0:
                raise KeyError(f"edge endpoint missing: {src} -[{etype}]-> {dst}")

    def delete_edge(self, src: str, etype: str, dst: str) -> None:
        self._check(etype=etype)
        with self.driver.session() as s:
            s.run(f"MATCH (:Node {{id:$src}})-[r:{etype}]->(:Node {{id:$dst}}) DELETE r", src=src, dst=dst)

    def get_node(self, node_id: str) -> dict | None:
        with self.driver.session() as s:
            rec = s.run("MATCH (n:Node {id:$id}) RETURN n", id=node_id).single()
        return self._node(rec["n"]) if rec else None

    def edges(self, node_id: str, etype: str, direction: str) -> list[tuple[dict, dict]]:
        self._check(etype=etype)
        pat = f"(n:Node {{id:$id}})-[r:{etype}]->(m:Node)" if direction == "out" \
            else f"(n:Node {{id:$id}})<-[r:{etype}]-(m:Node)"
        with self.driver.session() as s:
            recs = list(s.run(f"MATCH {pat} RETURN r, m ORDER BY m.id", id=node_id))
        return [({**self._decode(dict(r["r"])), "type": etype}, self._node(r["m"])) for r in recs]

    def nodes_by_label(self, label: str) -> list[dict]:
        self._check(label=label)
        with self.driver.session() as s:
            return [self._node(r["n"]) for r in s.run(f"MATCH (n:{label}) RETURN n ORDER BY n.id")]

    def search(self, query: str, labels: list[str] | None = None, limit: int = 25) -> list[dict]:
        q = normalize_text(query)
        if not q:
            return []
        labels = labels or SEARCHABLE_LABELS
        for l in labels:
            self._check(label=l)
        where = " OR ".join(f"n:{l}" for l in labels)
        with self.driver.session() as s:
            recs = s.run(f"MATCH (n:Node) WHERE ({where}) AND n.search CONTAINS $q "
                         f"RETURN n ORDER BY size(n.name) LIMIT $lim", q=q, lim=limit)
            return [self._node(r["n"]) for r in recs]

    def all_edges(self) -> list[dict]:
        with self.driver.session() as s:
            recs = s.run("MATCH (a:Node)-[r]->(b:Node) RETURN a.id AS src, type(r) AS t, b.id AS dst, r "
                         "ORDER BY src, t, dst")
            return [{"src": x["src"], "type": x["t"], "dst": x["dst"], **self._decode(dict(x["r"]))} for x in recs]

    def counts(self) -> dict:
        with self.driver.session() as s:
            n = {r["l"]: r["c"] for r in s.run(
                "MATCH (n:Node) UNWIND [l IN labels(n) WHERE l <> 'Node'] AS l RETURN l, count(*) AS c")}
            e = {r["t"]: r["c"] for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
        return {"nodes": n, "edges": e}

    def close(self) -> None:
        self.driver.close()


def open_store(settings) -> GraphStore:
    if settings.graph_backend == "neo4j":
        if not settings.neo4j_password:
            raise RuntimeError("GRAPH_BACKEND=neo4j requires NEO4J_PASSWORD")
        return Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    return SqliteGraphStore(Path(settings.data_dir) / "graph.sqlite3")
