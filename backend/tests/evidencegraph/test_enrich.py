import json
from types import SimpleNamespace

import httpx
import pytest

from evidencegraph.cli import main as cli_main
from evidencegraph.enrich import Enricher, dry_run, quote_in_text
from evidencegraph.fetch import Fetcher
from evidencegraph.intake import run_intake
from evidencegraph.registry import load_registry
from polgraph.config import Settings as PolgraphSettings
from polgraph.exa_gateway import ExaGateway
from socialclaims.pastdev import PastDevClient
from test_intake import PID, _base_run, _claim

GOV = "https://www.gob.mx/sspc/prensa/homicidios-octubre"
NEWS = "https://www.animalpolitico.com/seguridad/homicidios-cdmx"
BLOG = "https://blog.example/opinion"
SOCIAL = "https://www.tiktok.com/@someone/video/1"
PAGES = {
    GOV: "<html><head><title>Boletin SSPC</title></head><body><p>Los homicidios dolosos en la Ciudad de Mexico "
         "bajaron 12 por ciento en octubre de 2026 respecto al mismo mes de 2025.</p></body></html>",
    NEWS: "<html><head><title>Homicidios</title></head><body><p>Organizaciones civiles reportaron que los homicidios "
          "aumentaron en tres alcaldias durante octubre.</p></body></html>",
    BLOG: "<html><head><title>Opinion</title></head><body><p>Un texto sin datos.</p></body></html>",
}
ANSWERS = {
    GOV: {"stance": "supports", "quote": "Los homicidios dolosos en la Ciudad de Mexico bajaron 12 por ciento en octubre "
                                         "de 2026 respecto al mismo mes de 2025."},
    NEWS: {"stance": "context", "quote": "Organizaciones civiles reportaron que los homicidios aumentaron en tres "
                                         "alcaldias durante octubre."},
    BLOG: {"stance": "contradicts", "quote": "Los homicidios subieron 40 por ciento en toda la ciudad."},
}


class NoWait:
    def wait(self):
        return None


class FakeLLM:
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def extract(self, system, prompt, tool):
        if "queries" in tool["input_schema"]["properties"]:
            self.calls.append("plan")
            return {"queries": [
                {"row": "neutral", "query": "homicidios CDMX octubre 2026", "skip": False, "skip_reason": ""},
                {"row": "primary", "query": "boletin homicidios CDMX", "skip": False, "skip_reason": ""},
                {"row": "indicator", "query": "", "skip": True, "skip_reason": "no statistic"}]}, {}
        url = json.loads(prompt)["page"]["url"]
        self.calls.append(url)
        return {"summary": f"Resumen propio de {url}.", "confidence": 0.8, "rationale": "Reason.",
                **ANSWERS[next(u for u in ANSWERS if u.rstrip("/") == url.rstrip("/"))]}, {"input_tokens": 10}


class FakeExa:
    def __init__(self):
        self.calls = []

    def search(self, query, **kw):
        self.calls.append(kw)
        urls = [GOV] if kw.get("include_domains") else [NEWS, SOCIAL, BLOG]
        return SimpleNamespace(results=[SimpleNamespace(url=u, title=u, published_date=None, author=None, score=0.5,
                                                        highlights=[]) for u in urls],
                               request_id=f"req-{len(self.calls)}", cost_dollars=SimpleNamespace(total=0.007))


class FakePast:
    def __init__(self):
        self.calls = []

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path.endswith("/identities"):
            return httpx.Response(200, json={"identities": []})
        if path.endswith("/audiences"):
            return httpx.Response(200, json={"audiences": []})
        if request.method == "PUT":
            return httpx.Response(200, json={})
        if path.endswith("/ingest/batch"):
            return httpx.Response(202, json={"ingestionId": f"ing-{len(self.calls)}", "items": []})
        return httpx.Response(200, json={"status": "completed"})

    def batches(self):
        return [b for m, p, b in self.calls if p.endswith("/ingest/batch")]


def _setup(conn):
    load_registry(conn)
    _base_run(conn)
    _claim(conn, "a", "prop-homicidios")
    run_intake(conn, [PID], mapper_version="m1")


def _enricher(conn, ev_settings, tmp_path, past, exa=None, llm=None):
    pages = []

    def page_handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        pages.append(str(request.url))
        return httpx.Response(200, text=PAGES[str(request.url)], headers={"content-type": "text/html"})

    fetcher = Fetcher(conn, ev_settings, client=httpx.Client(transport=httpx.MockTransport(page_handler)),
                      limiter_factory=lambda _h, _r: NoWait())
    gateway = ExaGateway(PolgraphSettings(_env_file=None, exa_api_key="k", data_dir=tmp_path), client=exa or FakeExa(),
                         db_path=tmp_path / "exa.sqlite3", limiter=NoWait(), budget_scope="ev", monthly_budget_usd=30)
    client = PastDevClient("key", transport=httpx.MockTransport(past.handler), sleep=lambda s: None)
    enricher = Enricher(conn, ev_settings, llm=llm or FakeLLM(), gateway=gateway, fetcher=fetcher, client=client,
                        sleep=lambda s: None)
    return enricher, pages


def test_dry_run_reads_only(conn, ev_settings):
    _setup(conn)
    out = dry_run(conn, ev_settings, None, 10)
    assert out["propositions"] == 1 and out["max_exa_searches"] == 7 and out["max_total_usd"] > 0
    assert conn.execute("SELECT status FROM ev_proposition_queue").fetchone()["status"] == "queued"
    assert conn.execute("SELECT count(*) AS n FROM ev_exa_request").fetchone()["n"] == 0


def test_enrich_searches_fetches_judges_and_projects(conn, ev_settings, tmp_path):
    _setup(conn)
    past, exa = FakePast(), FakeExa()
    enricher, pages = _enricher(conn, ev_settings, tmp_path, past, exa=exa)
    out = enricher.run(None, 5)
    assert not out["errors"]
    result = out["results"][0]
    assert result["status"] == "mapped" and result["queries"] == 2
    assert result["stances"] == {"supports": 1, "context": 1, "unverified": 1}
    assert SOCIAL not in pages and not any(c.get("contents") for c in exa.calls)
    assert all("tiktok.com" in c["exclude_domains"] for c in exa.calls if not c.get("include_domains"))

    links = {r["document_id"]: r for r in conn.execute("SELECT * FROM ev_evidence_link")}
    assert len(links) == 3 and sum(r["quote_verified"] for r in links.values()) == 2

    [batch] = past.batches()
    kinds = [it["metadata"]["kind"] for it in batch["items"]]
    assert kinds == ["claim", "document", "document", "evidence_link", "evidence_link"]
    claim, link = batch["items"][0], batch["items"][3]
    assert claim["audience"] == "pol-claudia-sheinbaum-pardo" and link["audience"] == claim["audience"]
    assert all("audience" not in it for it in batch["items"] if it["metadata"]["kind"] == "document")
    assert "Resumen propio" in batch["items"][1]["content"]
    assert not any("blog.example" in it["content"] for it in batch["items"])
    assert {r["status"] for r in conn.execute("SELECT status FROM ev_memory_record")} == {"completed"}
    assert conn.execute("SELECT count(*) AS n FROM ev_memory_record").fetchone()["n"] == 5
    assert conn.execute("SELECT status FROM ev_proposition_queue").fetchone()["status"] == "mapped"
    assert conn.execute("SELECT count(*) AS n FROM ev_exa_request").fetchone()["n"] == 2


def test_rerun_reuses_judgements_and_skips_unchanged_items(conn, ev_settings, tmp_path):
    _setup(conn)
    past, llm = FakePast(), FakeLLM()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, past, llm=llm)
    enricher.run(None, 5)
    conn.execute("UPDATE ev_proposition_queue SET status = 'queued'")
    llm.calls.clear()
    out = enricher.run(None, 5)
    assert llm.calls == ["plan"]
    assert out["results"][0]["pastdev"]["sent"] == 0 and len(past.batches()) == 1


def test_copyright_quote_limit():
    from evidencegraph.project import allowed_quote

    assert allowed_quote("x" * 400, "open_gov") == "x" * 400
    assert allowed_quote("x" * 301, "copyright") is None and allowed_quote("x" * 300, "copyright")
    assert allowed_quote("x" * 201, "unknown") is None
    assert quote_in_text("Los homicidios BAJARON 12%", "... los homicidios bajaron 12 % en octubre")


def test_enrich_requires_confirm_spend(monkeypatch):
    with pytest.raises(SystemExit):
        cli_main(["enrich", "--limit", "1"])
