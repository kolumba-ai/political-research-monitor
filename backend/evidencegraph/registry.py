"""Reviewed source registry and country seed loading."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from polgraph.evidence import authority_tier

REGISTRY_DIR = Path(__file__).parent / "registry"
LICENCE_BY_CLASS = {
    "official_statistics": "open_gov",
    "government_publication": "open_gov",
    "legislative_judicial": "open_gov",
    "news": "copyright",
    "fact_check": "copyright",
    "think_tank_ngo": "copyright",
    "polling": "copyright",
    "academic": "copyright",
}


def _seed_rows(country: str) -> list[dict]:
    data = json.loads((REGISTRY_DIR / f"{country.upper()}.json").read_text())
    rows = []
    for item in data["domains"]:
        domain = item["key"]
        licence = item.get("licence_class") or LICENCE_BY_CLASS[item["class"]]
        rows.append({
            "domain": domain,
            "source_class": item["class"],
            "publisher": item.get("publisher") or domain.split("/")[0],
            "authority_tier": item.get("authority_tier", authority_tier("https://" + domain)),
            "licence_class": licence,
            "can_store_full_text": licence in ("open_gov", "open_access"),
            "robots_status": None,
            "crawl_delay": None,
            "feed_type": None,
            "feed_url": None,
            "is_claimant_for": item.get("claimants") or [],
            "list": "allow",
            "notes": item.get("notes"),
            "added_by": "seed:appendix-a",
        })
    for domain in data.get("blocked", []):
        rows.append({
            "domain": domain,
            "source_class": None,
            "publisher": domain,
            "authority_tier": 3,
            "licence_class": "unknown",
            "can_store_full_text": False,
            "robots_status": None,
            "crawl_delay": None,
            "feed_type": None,
            "feed_url": None,
            "is_claimant_for": [],
            "list": "block",
            "notes": None,
            "added_by": "seed:appendix-a",
        })
    return rows


def load_registry(conn, country: str = "MX") -> int:
    rows = _seed_rows(country)
    sql = (
        "INSERT INTO ev_source_registry(domain, source_class, publisher, authority_tier, licence_class, "
        "can_store_full_text, robots_status, crawl_delay, feed_type, feed_url, is_claimant_for, list, notes, "
        "added_by, added_at, reviewed_at) VALUES "
        "(%(domain)s, %(source_class)s, %(publisher)s, %(authority_tier)s, %(licence_class)s, "
        "%(can_store_full_text)s, %(robots_status)s, %(crawl_delay)s, %(feed_type)s, %(feed_url)s, "
        "%(is_claimant_for)s, %(list)s, %(notes)s, %(added_by)s, now(), NULL) "
        "ON CONFLICT (domain) DO UPDATE SET source_class = EXCLUDED.source_class, publisher = EXCLUDED.publisher, "
        "authority_tier = EXCLUDED.authority_tier, licence_class = EXCLUDED.licence_class, "
        "can_store_full_text = EXCLUDED.can_store_full_text, robots_status = EXCLUDED.robots_status, "
        "crawl_delay = EXCLUDED.crawl_delay, feed_type = EXCLUDED.feed_type, feed_url = EXCLUDED.feed_url, "
        "is_claimant_for = EXCLUDED.is_claimant_for, list = EXCLUDED.list, notes = EXCLUDED.notes, "
        "added_by = EXCLUDED.added_by WHERE ev_source_registry.reviewed_at IS NULL"
    )
    with conn.transaction():
        for row in rows:
            conn.execute(sql, row)
    return len(rows)


def _host_path(value: str) -> tuple[str, str]:
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host, parsed.path.rstrip("/").lower()


def lookup(conn, url: str) -> dict | None:
    host, path = _host_path(url)
    matches = []
    for row in conn.execute("SELECT * FROM ev_source_registry").fetchall():
        key_host, key_path = _host_path(row["domain"])
        host_matches = host == key_host or host.endswith("." + key_host)
        if host_matches and (not key_path or path.startswith(key_path)):
            matches.append((len(key_host) + len(key_path), row))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def exclude_domains(conn) -> list[str]:
    return [row["domain"] for row in conn.execute(
        "SELECT domain FROM ev_source_registry WHERE list = 'block' ORDER BY domain"
    ).fetchall()]
