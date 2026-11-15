import httpx

from socialclaims import thumbnails
from socialclaims.capture import Capturer

from .conftest import FIXED_NOW, FakeOriane, make_result

PID = "person:MX:claudia-sheinbaum-pardo"
URL = "https://social-catalog-static.oriane.xyz/oriane-contents/tiktok/{}/thumbnail.webp"


def _capture(conn, graph, sc_settings, results):
    Capturer(conn, graph, FakeOriane(results).client(), sc_settings, clock=lambda: FIXED_NOW).capture(PID)


def _results():
    return [make_result(1, thumbnailMediaUrl=URL.format(1), thumbnailMediaId="med_1"),
            make_result(2, format="image", thumbnailMediaUrl=URL.format(2), thumbnailMediaId="med_2"),
            make_result(3)]


def test_capture_stores_thumbnail(conn, graph, sc_settings):
    _capture(conn, graph, sc_settings, _results())
    rows = {r["oriane_id"]: r for r in conn.execute("SELECT oriane_id, thumbnail_url, thumbnail_media_id FROM post")}
    assert rows[make_result(1)["id"]]["thumbnail_url"] == URL.format(1)
    assert rows[make_result(1)["id"]]["thumbnail_media_id"] == "med_1"
    assert rows[make_result(3)["id"]]["thumbnail_url"] is None


def test_backfill_from_raw_payloads(conn, graph, sc_settings):
    _capture(conn, graph, sc_settings, _results())
    conn.execute("UPDATE post SET thumbnail_url = NULL, thumbnail_media_id = NULL")
    out = thumbnails.backfill(conn, sc_settings.raw_storage_dir)
    assert out == {"candidates": 3, "filled": 2, "missing_payload": 0, "no_thumbnail": 1}
    row = conn.execute("SELECT thumbnail_url, thumbnail_media_id FROM post WHERE oriane_id = %s",
                       (make_result(2)["id"],)).fetchone()
    assert row == {"thumbnail_url": URL.format(2), "thumbnail_media_id": "med_2"}


def test_download_filters_format_and_is_idempotent(conn, graph, sc_settings, tmp_path):
    _capture(conn, graph, sc_settings, _results())
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=b"webp-bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = thumbnails.download(conn, tmp_path / "thumbs", formats=["video"], client=client)
    assert out["candidates"] == 1 and out["saved"] == 1 and out["failed"] == []
    assert seen == [URL.format(1)]
    rel = conn.execute("SELECT thumbnail_path FROM post WHERE oriane_id = %s", (make_result(1)["id"],)).fetchone()
    assert rel["thumbnail_path"] == f"tiktok/{make_result(1)['platformId']}.webp"
    assert (tmp_path / "thumbs" / rel["thumbnail_path"]).read_bytes() == b"webp-bytes"
    assert thumbnails.download(conn, tmp_path / "thumbs", formats=["video"], client=client)["candidates"] == 0


def test_download_records_failures(conn, graph, sc_settings, tmp_path):
    _capture(conn, graph, sc_settings, _results())
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    out = thumbnails.download(conn, tmp_path / "thumbs", client=client)
    assert out["saved"] == 0 and len(out["failed"]) == 2
    assert conn.execute("SELECT count(*) c FROM post WHERE thumbnail_path IS NOT NULL").fetchone()["c"] == 0
