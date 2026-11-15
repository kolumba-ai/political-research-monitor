"""`politician_account`: social handles owned by System B. Oriane profile search only *proposes* candidates;
an account becomes `verified` only by explicit human approval citing at least one independent source."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from .oriane import CreditsExhausted, OrianeClient, credits_for
from .query import name_variants
from .systema import PersonSnapshot


def profile_search_body(snap: PersonSnapshot, platforms: list[str]) -> dict:
    variants = name_variants(snap.name, snap.aliases)
    return {"operator": "and",
            "filters": {"platform": {"includes": sorted(platforms)}},
            "queries": [{"operator": "or", "filters": {
                "displayName": {"includesExactly": {"values": variants, "operator": "or"}}}}]}


def discover(conn, client: OrianeClient, snap: PersonSnapshot, platforms: list[str], limit: int = 10,
             projection: str = "default", monthly_ceiling: int | None = None) -> list[dict]:
    if monthly_ceiling is not None:
        from .capture import month_credits_used, utcnow
        limit = min(limit, (monthly_ceiling - month_credits_used(conn, utcnow())) // credits_for(1, projection))
        if limit < 1:
            raise CreditsExhausted(402, "local monthly Oriane credit ceiling reached (no call made)")
    body = profile_search_body(snap, platforms)
    resp = client.search_profiles(body, limit=limit, projection=projection)
    rows = resp.results
    proposed = []
    with conn.transaction():
        conn.execute("INSERT INTO oriane_usage(endpoint, purpose, projection, http_status, request_id, rows, credits) "
                     "VALUES ('/rest/profiles/search', 'handle_discovery', %s, %s, %s, %s, %s)",
                     (projection, resp.status, resp.request_id, len(rows), credits_for(len(rows), projection)))
        for p in rows:
            handle = (p.get("handle") or "").lstrip("@").lower()
            if not handle:
                continue
            profile = {k: p.get(k) for k in ("displayName", "followersCount", "isVerified", "bio", "bioLink",
                                              "platformId", "postsCount")}
            conn.execute(
                "INSERT INTO politician_account(politician_id, platform, handle, oriane_profile_id, profile, notes) "
                "VALUES (%s,%s,%s,%s,%s,'proposed by Oriane profile search') "
                "ON CONFLICT (politician_id, platform, handle) DO UPDATE SET profile = EXCLUDED.profile, "
                "oriane_profile_id = COALESCE(politician_account.oriane_profile_id, EXCLUDED.oriane_profile_id)",
                (snap.politician_id, p.get("platform"), handle, p.get("id"), Jsonb(profile)))
            proposed.append({"platform": p.get("platform"), "handle": handle, **profile})
    return proposed


def approve(conn, politician_id: str, platform: str, handle: str, approved_by: str, sources: list[str],
            oriane_profile_id: str | None = None) -> None:
    """Mark an account official. Requires a named approver and an independent source (official site,
    institutional directory, ...); a verified badge, bio or follower count alone is not enough."""
    if not approved_by:
        raise ValueError("approved_by is required")
    if not sources:
        raise ValueError("at least one independent source URL is required")
    handle = handle.lstrip("@").lower()
    with conn.transaction():
        conn.execute(
            "INSERT INTO politician_account(politician_id, platform, handle, oriane_profile_id, status, sources, "
            "approved_by, first_verified_at, last_verified_at) VALUES (%s,%s,%s,%s,'verified',%s,%s,now(),now()) "
            "ON CONFLICT (politician_id, platform, handle) DO UPDATE SET status = 'verified', "
            "sources = EXCLUDED.sources, approved_by = EXCLUDED.approved_by, "
            "oriane_profile_id = COALESCE(EXCLUDED.oriane_profile_id, politician_account.oriane_profile_id), "
            "first_verified_at = COALESCE(politician_account.first_verified_at, now()), last_verified_at = now()",
            (politician_id, platform, handle, oriane_profile_id, Jsonb(sources), approved_by))


def reject(conn, politician_id: str, platform: str, handle: str, reviewer: str, note: str = "") -> None:
    with conn.transaction():
        conn.execute("UPDATE politician_account SET status = 'rejected', approved_by = %s, notes = %s "
                     "WHERE politician_id = %s AND platform = %s AND handle = %s",
                     (reviewer, note, politician_id, platform, handle.lstrip("@").lower()))
