"""Post thumbnails: backfill Oriane's thumbnailMediaUrl from saved raw payloads and download local copies.

Neither step calls the Oriane API; downloads hit Oriane's public static CDN, which is not billed."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from .segments import load_post


def backfill(conn, raw_dir: Path | str) -> dict:
    """Fill post.thumbnail_url/thumbnail_media_id from each post's latest stored raw payload."""
    rows = conn.execute(
        "SELECT p.oriane_id, d.payload_path FROM post p JOIN raw_data d ON d.raw_id = p.latest_raw_id "
        "WHERE p.thumbnail_url IS NULL ORDER BY d.payload_path").fetchall()
    cache: dict = {}
    filled = missing_payload = no_thumbnail = 0
    for r in rows:
        if not r["payload_path"]:
            missing_payload += 1
            continue
        try:
            post = load_post(raw_dir, r["payload_path"], cache)
        except FileNotFoundError:
            missing_payload += 1
            continue
        if not post.get("thumbnailMediaUrl"):
            no_thumbnail += 1
            continue
        conn.execute("UPDATE post SET thumbnail_url = %s, thumbnail_media_id = %s WHERE oriane_id = %s",
                     (post["thumbnailMediaUrl"], post.get("thumbnailMediaId"), r["oriane_id"]))
        filled += 1
    return {"candidates": len(rows), "filled": filled, "missing_payload": missing_payload,
            "no_thumbnail": no_thumbnail}


def _rel_path(platform: str, platform_id: str | None, oriane_id: str, url: str) -> str:
    ext = Path(urlparse(url).path).suffix or ".img"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (platform_id or oriane_id))
    return f"{platform}/{safe}{ext}"


def download(conn, out_dir: Path | str, *, formats: list[str] | None = None, client: httpx.Client | None = None,
             redownload: bool = False) -> dict:
    """Save each post's thumbnail under out_dir/<platform>/<platform_id>.<ext> and record thumbnail_path."""
    out_dir = Path(out_dir)
    sql = "SELECT oriane_id, platform, platform_id, thumbnail_url FROM post WHERE thumbnail_url IS NOT NULL"
    params: list = []
    if formats:
        sql += " AND format = ANY(%s)"
        params.append(formats)
    if not redownload:
        sql += " AND thumbnail_path IS NULL"
    rows = conn.execute(sql + " ORDER BY oriane_id", params).fetchall()
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    saved = skipped = 0
    failed: list[dict] = []
    try:
        for r in rows:
            rel = _rel_path(r["platform"], r["platform_id"], r["oriane_id"], r["thumbnail_url"])
            dest = out_dir / rel
            if dest.exists() and not redownload:
                skipped += 1
            else:
                try:
                    resp = http.get(r["thumbnail_url"])
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    failed.append({"oriane_id": r["oriane_id"], "error": str(exc)[:200]})
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                saved += 1
            conn.execute("UPDATE post SET thumbnail_path = %s WHERE oriane_id = %s", (rel, r["oriane_id"]))
    finally:
        if client is None:
            http.close()
    return {"candidates": len(rows), "saved": saved, "already_present": skipped, "failed": failed}
