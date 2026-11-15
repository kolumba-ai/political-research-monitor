import builtins
from io import BytesIO
from pathlib import Path

import httpx
from pypdf import PdfWriter

from evidencegraph import fetch as fetch_module
from evidencegraph.fetch import Fetcher, _PageParser, _date_metadata
from evidencegraph.registry import load_registry
from polgraph.ids import source_id


class NoWait:
    def wait(self):
        return None


def _fetcher(conn, settings, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return Fetcher(conn, settings, client=client, limiter_factory=lambda _host, _rps: NoWait()), client


def test_robots_disallow_and_http_blocked(conn, ev_settings):
    load_registry(conn)

    def handler(request):
        if request.url.path == "/robots.txt":
            if request.url.host == "robots-5xx.example":
                return httpx.Response(503)
            if request.url.host == "robots-no.example":
                return httpx.Response(200, text="User-agent: *\nDisallow: /blocked")
            return httpx.Response(404)
        return httpx.Response(403)

    fetcher, client = _fetcher(conn, ev_settings, handler)
    assert fetcher.fetch("https://robots-no.example/blocked")["fetch_status"] == "robots_disallowed"
    assert fetcher.fetch("https://robots-5xx.example/page")["fetch_status"] == "robots_disallowed"
    blocked = fetcher.fetch("https://publisher.example/page")
    assert blocked["fetch_status"] == "blocked"
    assert conn.execute("SELECT fetch_status FROM ev_document WHERE document_id=%s",
                        (blocked["document_id"],)).fetchone()["fetch_status"] == "blocked"
    client.close()


def test_canonical_redirect_dates_versions_and_open_gov_snapshot(conn, ev_settings):
    load_registry(conn)
    bodies = [
        '<html lang="es"><head><title>Official data</title><link rel="canonical" href="/canonical">'
        '<meta property="article:published_time" content="2024-01-01">'
        '<script type="application/ld+json">{"datePublished":"2025-02-03","dateModified":"2025-02-04"}'
        '</script></head><body>INEGI reports an important official figure.</body></html>',
        '<html lang="es"><head><title>Official data</title><link rel="canonical" href="/canonical">'
        '<meta property="article:published_time" content="2024-01-01">'
        '<script type="application/ld+json">{"datePublished":"2025-02-03","dateModified":"2025-02-04"}'
        '</script></head><body>INEGI reports an important official figure.</body></html>',
        '<html lang="es"><head><title>Official data</title><link rel="canonical" href="/canonical">'
        '</head><body>INEGI reports a changed official figure.</body></html>',
    ]
    current = [0]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/canonical"})
        body = bodies[min(current[0], len(bodies) - 1)]
        current[0] += 1
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    fetcher, client = _fetcher(conn, ev_settings, handler)
    original_url = "https://www.inegi.org.mx/old"
    first = fetcher.fetch(original_url)
    assert first["fetch_status"] == "fetched"
    assert first["date_origin"] == "jsonld"
    assert first["snapshot_stored"] is True
    assert first["resolved_document_id"] == source_id("https://www.inegi.org.mx/canonical")
    assert conn.execute("SELECT count(*) AS n FROM ev_kg_edge WHERE type='SAME_AS'").fetchone()["n"] == 1
    assert conn.execute("SELECT published_at FROM ev_document WHERE document_id=%s",
                        (first["document_id"],)).fetchone()["published_at"].year == 2025
    fetcher.fetch(original_url)
    assert conn.execute("SELECT count(*) AS n FROM ev_document_version WHERE document_id=%s",
                        (first["document_id"],)).fetchone()["n"] == 1
    fetcher.fetch(original_url)
    assert conn.execute("SELECT count(*) AS n FROM ev_document_version WHERE document_id=%s",
                        (first["document_id"],)).fetchone()["n"] == 2
    client.close()


def test_copyright_pages_are_not_snapshotted_and_duplicates_cluster(conn, ev_settings):
    load_registry(conn)

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "www.eluniversal.com.mx":
            return httpx.Response(200, text="<html><body>Copyright news text.</body></html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, text="<html><body>Same official report about public services.</body></html>",
                              headers={"content-type": "text/html"})

    fetcher, client = _fetcher(conn, ev_settings, handler)
    copyright_result = fetcher.fetch("https://www.eluniversal.com.mx/article")
    assert copyright_result["fetch_status"] == "fetched"
    assert copyright_result["snapshot_path"] is None
    assert conn.execute("SELECT snapshot_path FROM ev_document_version WHERE document_id=%s",
                        (copyright_result["document_id"],)).fetchone()["snapshot_path"] is None
    first = fetcher.fetch("https://www.inegi.org.mx/a")
    second = fetcher.fetch("https://www.inegi.org.mx/b")
    first_doc = conn.execute("SELECT dup_cluster_id FROM ev_document WHERE document_id=%s",
                             (first["document_id"],)).fetchone()
    second_doc = conn.execute("SELECT dup_cluster_id FROM ev_document WHERE document_id=%s",
                              (second["document_id"],)).fetchone()
    assert second_doc["dup_cluster_id"] == (first_doc["dup_cluster_id"] or first["document_id"])
    client.close()


def test_date_origin_precedence(conn, ev_settings):
    parser = _PageParser()
    parser.feed('<meta property="article:published_time" content="2023-01-01">'
                '<meta name="date" content="2022-01-01">')
    published, _, origin, confidence = _date_metadata(parser, "https://news.example/2020/02/03/story",
                                                        "2019-01-01")
    assert published.year == 2023
    assert (origin, confidence) == ("open_graph", "high")

    parser = _PageParser()
    parser.feed('<meta name="citation_publication_date" content="2022-03-04">')
    assert _date_metadata(parser, "https://news.example/2020-02-03/story", None)[2:] == ("meta", "high")
    assert _date_metadata(_PageParser(), "https://news.example/2020/02/03/story", "2019-01-01")[2:] == (
        "url", "medium")
    assert _date_metadata(_PageParser(), "https://news.example/story", "2019-01-01")[2:] == ("exa", "low")
    assert _date_metadata(_PageParser(), "https://news.example/story", None)[2:] == ("none", "none")


def test_pdf_text_page_markers_are_snapshotted_for_open_gov(conn, ev_settings):
    load_registry(conn)
    pdf_buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_buffer)
    pdf_bytes = pdf_buffer.getvalue()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=pdf_bytes, headers={"content-type": "application/pdf"})

    fetcher, client = _fetcher(conn, ev_settings, handler)
    result = fetcher.fetch("https://www.gob.mx/report.pdf")

    assert result["fetch_status"] == "fetched"
    assert result["snapshot_stored"] is True
    version = conn.execute(
        "SELECT content_sha256, snapshot_path FROM ev_document_version WHERE document_id=%s",
        (result["document_id"],),
    ).fetchone()
    assert version is not None
    assert version["content_sha256"] == result["content_sha256"]
    assert "--- page 1 ---" in Path(version["snapshot_path"]).read_text()
    client.close()


def test_missing_trafilatura_fallback_warns_once(monkeypatch):
    original_import = builtins.__import__
    warnings = []

    def import_without_trafilatura(name, *args, **kwargs):
        if name == "trafilatura":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_trafilatura)
    monkeypatch.setattr(fetch_module.log, "warning", lambda event: warnings.append(event))
    monkeypatch.setattr(fetch_module, "_TRAFILATURA_WARNING_EMITTED", False)
    parser = _PageParser()
    parser.feed("<html><body>Fallback text.</body></html>")

    assert fetch_module._html_text(b"<html><body>Fallback text.</body></html>", parser) == "Fallback text."
    fetch_module._html_text(b"<html><body>Fallback text.</body></html>", parser)

    assert warnings == ["fetch.trafilatura_missing"]
