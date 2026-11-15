"""Phase 0: versioned `politician_ref` snapshots of System A identities, reconciliation and ID redirects.

A new version is written whenever the identity (name, aliases, offices, terms, parties, pipeline_mode) or the
stored query changes. Old versions are kept (`superseded_at`), and runs/posts reference the version they were
captured under, so historical records are never silently relabelled.
"""
from __future__ import annotations

import json

from polgraph.ids import normalize_text
from psycopg.types.json import Jsonb

from .query import QueryTerms, build_query, name_variants, query_id
from .systema import GraphReader, PersonSnapshot, read_person

COUNTRY_LANGUAGES = {"MX": ["es"]}


def resolve_redirect(conn, politician_id: str) -> str:
    seen = {politician_id}
    while True:
        row = conn.execute("SELECT new_id FROM person_id_redirect WHERE old_id = %s", (politician_id,)).fetchone()
        if not row:
            return politician_id
        politician_id = row["new_id"]
        if politician_id in seen:
            raise ValueError(f"redirect cycle at {politician_id}")
        seen.add(politician_id)


def add_redirect(conn, old_id: str, new_id: str, reason: str, created_by: str) -> None:
    """Record a System A identity merge. Existing rows keep `old_id`; readers follow the redirect."""
    if old_id == new_id or resolve_redirect(conn, new_id) == old_id:
        raise ValueError("redirect would create a cycle")
    with conn.transaction():
        conn.execute("INSERT INTO person_id_redirect(old_id, new_id, reason, created_by) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (old_id) DO UPDATE SET new_id = EXCLUDED.new_id, reason = EXCLUDED.reason, "
                     "created_by = EXCLUDED.created_by, created_at = now()", (old_id, new_id, reason, created_by))
        conn.execute("INSERT INTO reconciliation_job(politician_id, kind, diff) VALUES (%s, 'id_redirected', %s)",
                     (old_id, Jsonb({"new_id": new_id, "reason": reason})))


def merged_ids(conn, politician_id: str) -> list[str]:
    """`politician_id` plus every old id that redirects to it, directly or transitively."""
    ids, frontier = [politician_id], [politician_id]
    while frontier:
        rows = conn.execute("SELECT old_id FROM person_id_redirect WHERE new_id = ANY(%s)", (frontier,)).fetchall()
        frontier = [r["old_id"] for r in rows if r["old_id"] not in ids]
        ids += frontier
    return ids


def load_terms(conn, politician_id: str) -> QueryTerms:
    terms = QueryTerms()
    ids = merged_ids(conn, politician_id)
    for r in conn.execute("SELECT DISTINCT platform, handle, oriane_profile_id FROM politician_account "
                          "WHERE politician_id = ANY(%s) AND status = 'verified' ORDER BY platform, handle",
                          (ids,)).fetchall():
        terms.handles.setdefault(r["platform"], []).append(r["handle"])
        if r["oriane_profile_id"]:
            terms.profile_ids.append(r["oriane_profile_id"])
    for r in conn.execute("SELECT DISTINCT kind, value FROM query_term WHERE politician_id = ANY(%s) "
                          "ORDER BY kind, value", (ids,)).fetchall():
        {"variant": terms.extra_variants, "hashtag": terms.hashtags,
         "exclude_variant": terms.excluded_variants}[r["kind"]].append(r["value"])
    return terms


def add_term(conn, politician_id: str, kind: str, value: str, added_by: str, note: str | None = None) -> None:
    with conn.transaction():
        conn.execute("INSERT INTO query_term(politician_id, kind, value, added_by, note) VALUES (%s,%s,%s,%s,%s) "
                     "ON CONFLICT DO NOTHING", (politician_id, kind, value, added_by, note))


def relevance_context(snap: PersonSnapshot, terms: QueryTerms) -> dict:
    entities: list[str] = []
    for p in snap.parties:
        entities += [p.get("name") or "", p.get("acronym") or "", p.get("coalition") or ""]
    for o in snap.current_offices:
        entities += [o.title_local or "", *(o.keywords or [])]
    return {
        "full_names": sorted({normalize_text(v) for v in name_variants(snap.name, snap.aliases, terms.extra_variants)
                              if len(v.split()) >= 2}),
        "entities": sorted({normalize_text(e) for e in entities if e and len(e) >= 3}),
        "languages": COUNTRY_LANGUAGES.get(snap.country_code, []),
        "country_code": snap.country_code,
    }


def current_ref(conn, politician_id: str) -> dict | None:
    return conn.execute("SELECT * FROM politician_ref WHERE politician_id = %s AND superseded_at IS NULL",
                        (politician_id,)).fetchone()


def _diff(old: dict, new: dict) -> dict:
    return {k: {"from": old.get(k), "to": new.get(k)} for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)}


def sync_ref(conn, graph: GraphReader, politician_id: str, platforms: list[str]) -> dict:
    """Read the person from System A and write a new `politician_ref` version if anything changed."""
    pid = resolve_redirect(conn, politician_id)
    snap = read_person(graph, pid)
    prev = current_ref(conn, pid)
    if snap is None:
        with conn.transaction():
            conn.execute("INSERT INTO reconciliation_job(politician_id, kind, from_version) VALUES (%s,'id_missing',%s)",
                         (pid, prev["version"] if prev else None))
        return {"politician_id": pid, "status": "missing_in_system_a", "version": prev["version"] if prev else None}

    terms = load_terms(conn, pid)
    q = build_query(snap, terms, platforms)
    qid = query_id(q)
    sha = snap.snapshot_sha256()
    if prev and prev["snapshot_sha256"] == sha and prev["query_id"] == qid:
        return {"politician_id": pid, "status": "unchanged", "version": prev["version"],
                "tracking_status": prev["tracking_status"], "query_id": qid}

    version = (prev["version"] + 1) if prev else 1
    with conn.transaction():
        if prev:
            conn.execute("UPDATE politician_ref SET superseded_at = now() WHERE politician_id = %s AND version = %s",
                         (pid, prev["version"]))
        conn.execute(
            "INSERT INTO politician_ref(politician_id, person_updated_at, version, snapshot_sha256, graph_run, "
            "country_code, pipeline_mode, name, aliases, offices, parties, evidence_status, tracking_status, snapshot, "
            "query_json, query_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (pid, snap.person_updated_at, version, sha, snap.graph_run, snap.country_code, snap.pipeline_mode,
             snap.name, Jsonb(snap.aliases), Jsonb([o.__dict__ for o in snap.offices]), Jsonb(snap.parties),
             snap.evidence_status, snap.tracking_status, Jsonb(snap.to_dict()), Jsonb(q), qid))
        if prev and prev["snapshot_sha256"] != sha:
            old_fields = json.loads(json.dumps(prev["snapshot"]))
            diff = _diff({k: old_fields.get(k) for k in ("name", "aliases", "offices", "parties", "pipeline_mode")},
                         json.loads(json.dumps({k: snap.to_dict()[k] for k in
                                                ("name", "aliases", "offices", "parties", "pipeline_mode")})))
            kind = "pipeline_mode_changed" if set(diff) == {"pipeline_mode"} else "identity_changed"
            conn.execute("INSERT INTO reconciliation_job(politician_id, kind, from_version, to_version, diff) "
                         "VALUES (%s,%s,%s,%s,%s)", (pid, kind, prev["version"], version, Jsonb(diff)))
        if snap.tracking_status == "needs_review":
            conn.execute("INSERT INTO review(item_type, item_id, reason, payload) VALUES ('politician', %s, "
                         "'single_source_officeholding', %s) ON CONFLICT DO NOTHING",
                         (pid, Jsonb({"version": version})))
    return {"politician_id": pid, "status": "created" if not prev else "new_version", "version": version,
            "tracking_status": snap.tracking_status, "query_id": qid}
