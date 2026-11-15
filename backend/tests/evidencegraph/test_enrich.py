import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from evidencegraph.cli import main as cli_main
from evidencegraph.enrich import Enricher, dry_run, quote_in_text
from evidencegraph.fetch import Fetcher
from evidencegraph.intake import run_intake
from evidencegraph.project import _iso
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
    [claim] = batch["items"]  # one record per politician, the evidence folded in
    assert claim["metadata"]["kind"] == "claim" and claim["audience"] == "pol-claudia-sheinbaum-pardo"
    assert sorted(e["stance"] for e in _evidence(claim)) == ["context", "supports"]
    assert "Resumen propio" in claim["content"] and "SUPPORTS:" in claim["content"]
    assert "blog.example" not in claim["content"]
    assert {r["status"] for r in conn.execute("SELECT status FROM ev_memory_record")} == {"completed"}
    assert conn.execute("SELECT count(*) AS n FROM ev_memory_record").fetchone()["n"] == 1
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


class IrrelevantLLM(FakeLLM):
    def extract(self, system, prompt, tool):
        out, usage = super().extract(system, prompt, tool)
        return (out if "queries" in out else out | {"stance": "irrelevant", "quote": ""}), usage


def test_claim_without_verified_evidence_is_not_written(conn, ev_settings, tmp_path):
    _setup(conn)
    past = FakePast()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, past, llm=IrrelevantLLM())
    out = enricher.run(None, 5)
    assert out["results"][0]["status"] == "mapped" and past.batches() == []
    assert conn.execute("SELECT count(*) AS n FROM ev_memory_record").fetchone()["n"] == 0


def test_failed_ingestion_keeps_proposition_queued(conn, ev_settings, tmp_path):
    _setup(conn)

    class FailingPast(FakePast):
        def handler(self, request):
            if request.method == "GET" and "/ingest/" in request.url.path:
                self.calls.append(("GET", request.url.path, None))
                return httpx.Response(200, json={"status": "failed", "failure": {"code": "x"}})
            return super().handler(request)

    enricher, _ = _enricher(conn, ev_settings, tmp_path, FailingPast())
    out = enricher.run(None, 5)
    assert out["results"][0]["status"] == "queued" and out["propositions"] == 1
    assert conn.execute("SELECT status FROM ev_proposition_queue").fetchone()["status"] == "queued"
    assert {r["status"] for r in conn.execute("SELECT status FROM ev_memory_record")} == {"failed"}


def test_stale_mapping_is_reclaimed(conn, ev_settings, tmp_path):
    _setup(conn)
    conn.execute("UPDATE ev_proposition_queue SET status = 'mapping', updated_at = now() - interval '5 minutes'")
    assert dry_run(conn, ev_settings, None, None)["propositions"] == 0
    conn.execute("UPDATE ev_proposition_queue SET updated_at = now() - interval '2 hours'")
    assert dry_run(conn, ev_settings, None, None)["propositions"] == 1
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    assert enricher.run(None, None)["results"][0]["status"] == "mapped"


def test_only_latest_document_version_is_projected(conn, ev_settings, tmp_path):
    from evidencegraph.project import build_items

    _setup(conn)
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    enricher.run(None, 5)
    prop = conn.execute("SELECT * FROM ev_proposition_queue q JOIN proposition p USING (proposition_id)").fetchone()
    claims = conn.execute(__import__("evidencegraph.enrich", fromlist=["CLAIMS_SQL"]).CLAIMS_SQL,
                          (prop["proposition_id"],)).fetchall()
    gov = conn.execute("SELECT document_id FROM ev_evidence_link WHERE stance = 'supports'").fetchone()["document_id"]
    conn.execute("INSERT INTO ev_document_version(document_id, content_sha256, http_status, bytes, retrieved_at) "
                 "VALUES (%s, 'newer', 200, 1, now() + interval '1 hour')", (gov,))
    [item] = build_items(conn, prop, claims, ev_settings.mapper_version)
    assert [e["stance"] for e in _evidence(item)] == ["context"]


def _evidence(item: dict) -> list[dict]:
    return json.loads(item["content"].split("JSON:\n", 1)[1])["evidence"]


def test_evidence_is_timed_like_its_claim(conn, ev_settings, tmp_path):
    _setup(conn)
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    enricher.run(None, 5)
    [item] = _items(conn, ev_settings)
    stated = conn.execute("SELECT min(stated_at) AS s FROM distilled_claim").fetchone()["s"]
    assert item["timestamp"] == _iso(stated)
    evidence = _evidence(item)
    assessed = conn.execute("SELECT link_id, assessed_at FROM ev_evidence_link").fetchall()
    assert evidence and all(e["assessed_at"] == _iso({r["link_id"]: r["assessed_at"] for r in assessed}[e["link_id"]])
                            for e in evidence)


def _items(conn, ev_settings):
    from evidencegraph.enrich import CLAIMS_SQL
    from evidencegraph.project import build_items

    prop = conn.execute("SELECT * FROM ev_proposition_queue q JOIN proposition p USING (proposition_id)").fetchone()
    claims = conn.execute(CLAIMS_SQL, (prop["proposition_id"],)).fetchall()
    return build_items(conn, prop, claims, ev_settings.mapper_version)


def test_old_claim_links_fall_inside_the_claim_date_window(conn, ev_settings, tmp_path):
    _setup(conn)
    stated = datetime(2026, 9, 8, 22, 28, tzinfo=timezone.utc)
    conn.execute("UPDATE distilled_claim SET stated_at = %s", (stated,))
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    enricher.run(None, 5)
    links = [it for it in _items(conn, ev_settings) if _evidence(it)]
    window = (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc))
    assert links and all(window[0] <= datetime.fromisoformat(it["timestamp"].replace("Z", "+00:00")) <= window[1]
                         for it in links)
    assessed = conn.execute("SELECT min(assessed_at) AS a FROM ev_evidence_link").fetchone()["a"]
    assert assessed > window[1]


def test_shared_claim_links_take_each_politicians_own_date(conn, ev_settings, tmp_path):
    from test_intake import OTHER_PID

    load_registry(conn)
    _base_run(conn)
    _claim(conn, "a", "prop-homicidios")
    _claim(conn, "b", "prop-homicidios", pid=OTHER_PID)
    first, second = datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
    conn.execute("UPDATE distilled_claim SET stated_at = %s WHERE politician_id = %s", (first, PID))
    conn.execute("UPDATE distilled_claim SET stated_at = %s WHERE politician_id = %s", (second, OTHER_PID))
    run_intake(conn, [PID, OTHER_PID], mapper_version="m1")
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    enricher.run(None, 5)
    items = _items(conn, ev_settings)
    assert {it["metadata"]["politician_id"]: it["timestamp"] for it in items} == {PID: _iso(first),
                                                                                 OTHER_PID: _iso(second)}
    assert all(_evidence(it) == _evidence(items[0]) for it in items)


def test_reproject_resends_only_records_whose_date_changed(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import reproject

    _setup(conn)
    past = FakePast()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, past)
    enricher.run(None, 5)
    assert reproject(conn, None, ev_settings, dry_run=True)["items_changed"] == 0
    # A record written before a change to how records are built: simulate one stored with other content.
    lnk = conn.execute("SELECT datapoint_id FROM ev_memory_record WHERE kind = 'claim' LIMIT 1").fetchone()
    conn.execute("UPDATE ev_memory_record SET content_sha256 = 'timed-at-assessed_at' WHERE datapoint_id = %s",
                 (lnk["datapoint_id"],))
    before = len(past.batches())
    plan = reproject(conn, None, ev_settings, dry_run=True)
    assert plan["items_changed"] == 1 and plan["credits_estimate"] >= 1 and len(past.batches()) == before
    out = reproject(conn, enricher.client, ev_settings)
    assert not out["errors"] and out["credits_sent"] == plan["credits_estimate"]
    assert [it["id"] for it in past.batches()[-1]["items"]] == [lnk["datapoint_id"]]
    assert reproject(conn, None, ev_settings, dry_run=True)["items_changed"] == 0
    assert conn.execute("SELECT status FROM ev_proposition_queue").fetchone()["status"] == "mapped"


def test_reproject_resends_records_whose_ingestion_failed_later(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import reproject

    class FailingPast(FakePast):
        def handler(self, request):
            if request.method == "GET" and request.url.path.endswith("/ingest/ing-lost"):
                self.calls.append((request.method, request.url.path, None))
                return httpx.Response(200, json={"status": "failed", "failure": {"reason": "x"}})
            return super().handler(request)

    _setup(conn)
    past = FailingPast()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, past)
    enricher.run(None, 5)
    lnk = conn.execute("SELECT datapoint_id FROM ev_memory_record WHERE kind = 'claim' LIMIT 1").fetchone()
    # Sent by an earlier reproject whose ingestion was still processing; past.dev failed it afterwards.
    conn.execute("UPDATE ev_memory_record SET status = 'queued', ingestion_id = 'ing-lost' WHERE datapoint_id = %s",
                 (lnk["datapoint_id"],))
    out = reproject(conn, enricher.client, ev_settings)
    assert out["items_changed"] == 1 and not out["errors"]
    assert [it["id"] for it in past.batches()[-1]["items"]] == [lnk["datapoint_id"]]


def test_reproject_cli_requires_confirm_spend(capsys):
    with pytest.raises(SystemExit):
        cli_main(["reproject"])
    assert "confirm-spend" in capsys.readouterr().err


def test_pull_reads_system_c_claim_records():
    from socialclaims.pastdev import _evidence_claim_posts

    payload = {"format": "evidencegraph-claim/1", "politician": {"name": "X", "system_a_person_id": PID},
               "claims": [{"oriane_id": "o1", "text": "t1", "type": "statistic", "platform": "tiktok",
                           "url": "https://t/1", "stated_at": "2026-11-01T00:00:00Z", "quote": "q",
                           "relation_to_politician": "speaker"},
                          {"oriane_id": "o2", "text": "t2", "type": "factual", "platform": "instagram",
                           "url": "https://i/2", "stated_at": "2026-11-02T00:00:00Z", "quote": "q2",
                           "relation_to_politician": "mentioned"}]}
    posts = _evidence_claim_posts({"dataPointId": "clm-a-x"}, payload)
    assert [p[0] for p in posts] == [f"post:o1:{PID}", f"post:o2:{PID}"]
    assert posts[0][1]["source"]["url"] == "https://t/1" and posts[0][1]["claims"][0]["claim_es"] == "t1"
    assert posts[1][1]["claims"][0]["about_politician"] is False


def test_unchanged_items_still_processing_keep_proposition_queued(conn, ev_settings, tmp_path):
    _setup(conn)

    class SlowPast(FakePast):
        def handler(self, request):
            if request.method == "GET" and "/ingest/" in request.url.path:
                self.calls.append(("GET", request.url.path, None))
                return httpx.Response(200, json={"status": "processing"})
            return super().handler(request)

    past = SlowPast()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, past)
    import evidencegraph.project as projection
    real = projection.project
    projection.project = lambda *a, **k: real(*a, **{**k, "poll_timeout_s": 0})
    try:
        assert enricher.run(None, 5)["results"][0]["status"] == "queued"
        out = enricher.run(None, 5)
    finally:
        projection.project = real
    assert out["results"][0]["status"] == "queued" and len(past.batches()) == 1


def test_reverted_page_version_is_current_again(conn, ev_settings, tmp_path):
    from evidencegraph.project import LINKS_SQL

    _setup(conn)
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    enricher.run(None, 5)
    link = conn.execute("SELECT * FROM ev_evidence_link WHERE stance = 'supports'").fetchone()
    conn.execute("INSERT INTO ev_document_version(document_id, content_sha256, http_status, bytes, retrieved_at, "
                 "last_seen_at) VALUES (%s, 'other', 200, 1, now() + interval '1 hour', now() + interval '1 hour')",
                 (link["document_id"],))
    rows = lambda: [r["document_id"] for r in conn.execute(LINKS_SQL, (link["proposition_id"], link["mapper_version"]))]
    assert link["document_id"] not in rows()
    conn.execute("UPDATE ev_document_version SET last_seen_at = now() + interval '2 hours' "
                 "WHERE document_id = %s AND content_sha256 = %s", (link["document_id"], link["content_sha256"]))
    assert link["document_id"] in rows()


def test_pull_merges_one_post_across_propositions():
    from socialclaims.pastdev import _evidence_claim_posts

    a = _evidence_claim_posts({"dataPointId": "clm-a-x"}, {"politician": {"system_a_person_id": PID},
                                                          "claims": [{"oriane_id": "o1", "claim_id": "c1"}]})
    b = _evidence_claim_posts({"dataPointId": "clm-b-x"}, {"politician": {"system_a_person_id": PID},
                                                          "claims": [{"oriane_id": "o1", "claim_id": "c2"}]})
    assert a[0][0] == b[0][0]


def test_rows_setting_and_spend_cap(conn, ev_settings, tmp_path):
    _setup(conn)
    _claim(conn, "b", "prop-second")
    run_intake(conn, [PID], mapper_version="m1")
    settings = ev_settings.model_copy(update={"enrich_rows": "neutral"})
    assert dry_run(conn, settings, None, None)["max_exa_searches"] == 2
    exa = FakeExa()
    enricher, _ = _enricher(conn, settings, tmp_path, FakePast(), exa=exa)
    enricher.max_usd = 0.001
    out = enricher.run(None, None)
    assert out["propositions"] == 1 and out["stopped"] == "max_usd"
    assert len(exa.calls) == 1 and out["spend"]["exa_usd"] > 0 and out["spend"]["pastdev_credits"] > 0
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue WHERE status = 'queued'").fetchone()["n"] == 1


def test_run_stops_after_consecutive_failures(conn, ev_settings, tmp_path):
    from evidencegraph import enrich as enrich_module

    _setup(conn)
    for n in range(6):
        _claim(conn, f"f{n}", f"prop-f{n}")
    run_intake(conn, [PID], mapper_version="m1")

    class BrokeLLM(FakeLLM):
        def extract(self, system, prompt, tool):
            raise RuntimeError("anthropic 400: credit balance is too low")

    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast(), llm=BrokeLLM())
    out = enricher.run(None, None)
    assert out["stopped"] == "consecutive_failures" and len(out["errors"]) == enrich_module.MAX_CONSECUTIVE_FAILURES
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue WHERE status = 'queued'").fetchone()["n"] == 7


def test_unfinished_write_is_retried_without_new_search(conn, ev_settings, tmp_path):
    _setup(conn)

    class SlowThenDone(FakePast):
        slow = True

        def handler(self, request):
            if request.method == "GET" and "/ingest/" in request.url.path and self.slow:
                self.calls.append(("GET", request.url.path, None))
                return httpx.Response(200, json={"status": "processing"})
            return super().handler(request)

    past, llm, exa = SlowThenDone(), FakeLLM(), FakeExa()
    settings = ev_settings.model_copy(update={"enrich_poll_timeout_s": 0})
    enricher, _ = _enricher(conn, settings, tmp_path, past, exa=exa, llm=llm)
    assert enricher.run(None, 5)["results"][0]["status"] == "queued"
    past.slow, llm.calls, exa.calls = False, [], []
    out = enricher.run(None, 5)
    assert out["results"][0]["status"] == "mapped" and llm.calls == [] and exa.calls == []


def test_partially_judged_proposition_is_fully_rediscovered(conn, ev_settings, tmp_path):
    _setup(conn)

    class FailSecondPage(FakeLLM):
        pages = 0

        def extract(self, system, prompt, tool):
            if "queries" not in tool["input_schema"]["properties"]:
                self.pages += 1
                if self.pages == 2:
                    raise RuntimeError("boom")
            return super().extract(system, prompt, tool)

    llm = FailSecondPage()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast(), llm=llm)
    assert enricher.run(None, 5)["errors"]
    assert conn.execute("SELECT skip_reason FROM ev_proposition_queue").fetchone()["skip_reason"] is None
    llm.calls.clear()
    assert enricher.run(None, 5)["results"][0]["status"] == "mapped"
    assert llm.calls[0] == "plan" and len(llm.calls) >= 2


def test_pending_mark_survives_intake_and_needs_current_mapper(conn, ev_settings, tmp_path):
    _setup(conn)
    conn.execute("UPDATE ev_proposition_queue SET skip_reason = 'pastdev_pending'")
    run_intake(conn, [PID], mapper_version="m1")
    assert conn.execute("SELECT skip_reason FROM ev_proposition_queue").fetchone()["skip_reason"] == "pastdev_pending"
    llm = FakeLLM()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast(), llm=llm)
    assert enricher.run(None, 5)["results"][0]["status"] == "mapped"
    assert llm.calls[0] == "plan"  # no judgements of the current mapper yet: full discovery


def test_enrich_without_pastdev_judges_into_neon_and_leaves_the_write_pending(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import claim_next

    _setup(conn)
    past = FakePast()
    settings = ev_settings.model_copy(update={"enrich_pastdev": False})
    enricher, _ = _enricher(conn, settings, tmp_path, past)
    out = enricher.run(None, 5)
    assert not out["errors"] and out["results"][0]["pastdev"]["deferred"]
    assert not past.batches() and not any(p.endswith("/ingest/batch") for _, p, _ in past.calls)
    assert conn.execute("SELECT count(*) AS n FROM ev_evidence_link").fetchone()["n"] > 0
    q = conn.execute("SELECT status, skip_reason FROM ev_proposition_queue").fetchone()
    assert (q["status"], q["skip_reason"]) == ("queued", "pastdev_pending")
    assert claim_next(conn, None, settings.mapper_version, [], include_pastdev_pending=False) is None


class ProcessingPast(FakePast):
    """past.dev accepting batches but still processing them, until `done`."""
    done = False

    def handler(self, request):
        if request.method == "GET" and "/ingest/ing-" in request.url.path:
            self.calls.append((request.method, request.url.path, None))
            return httpx.Response(200, json={"status": "completed" if self.done else "processing"})
        return super().handler(request)


def _judged_pending(conn, ev_settings, tmp_path, past):
    _setup(conn)
    settings = ev_settings.model_copy(update={"enrich_pastdev": False})
    enricher, _ = _enricher(conn, settings, tmp_path, past)
    enricher.run(None, 5)
    return enricher


def test_sync_pastdev_waits_while_pastdev_queue_is_full(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import sync_pastdev

    past = FakePast()
    enricher = _judged_pending(conn, ev_settings, tmp_path, past)
    conn.execute("INSERT INTO ev_memory_record (datapoint_id, kind, past_project, status, content_sha256, "
                 "content_bytes, ingestion_id, memory_timestamp, sent_at) VALUES ('clm-other', 'claim', 'claims-mx', 'queued', 'x', 400000, "
                 "NULL, now(), now())")
    settings = ev_settings.model_copy(update={"pastdev_bytes_per_hour": 600_000, "pastdev_max_queue_minutes": 30})
    out = sync_pastdev(conn, enricher.client, settings)
    assert out["stopped"] == "pastdev_queue_full" and out["sent"] == 0 and not past.batches()
    assert conn.execute("SELECT skip_reason FROM ev_proposition_queue").fetchone()["skip_reason"] == "pastdev_pending"
    conn.execute("UPDATE ev_memory_record SET status = 'completed' WHERE datapoint_id = 'clm-other'")
    out = sync_pastdev(conn, enricher.client, settings)
    assert out["sent"] == 1 and out["mapped"] == 1 and len(past.batches()) == 1
    assert conn.execute("SELECT status FROM ev_proposition_queue").fetchone()["status"] == "mapped"


def test_sync_pastdev_does_not_resend_records_still_processing(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import sync_pastdev

    past = ProcessingPast()
    enricher = _judged_pending(conn, ev_settings, tmp_path, past)
    first = sync_pastdev(conn, enricher.client, ev_settings)
    assert first["sent"] == 1 and first["mapped"] == 0 and first["backlog_after"]["records"] == 1
    again = sync_pastdev(conn, enricher.client, ev_settings)
    assert again["candidates"] == 0 and len(past.batches()) == 1
    past.done = True
    last = sync_pastdev(conn, enricher.client, ev_settings)
    assert last["mapped"] == 1 and last["sent"] == 0 and len(past.batches()) == 1
    assert conn.execute("SELECT status FROM ev_proposition_queue").fetchone()["status"] == "mapped"


def test_sync_pastdev_dry_run_sends_nothing(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import sync_pastdev

    past = FakePast()
    _judged_pending(conn, ev_settings, tmp_path, past)
    out = sync_pastdev(conn, None, ev_settings, dry_run=True)
    assert out["propositions"] == 1 and out["sent"] == 0 and not past.batches()


def test_reproject_defer_leaves_changed_records_to_sync(conn, ev_settings, tmp_path):
    from evidencegraph.enrich import reproject

    _setup(conn)
    past = FakePast()
    enricher, _ = _enricher(conn, ev_settings, tmp_path, past)
    enricher.run(None, 5)
    conn.execute("UPDATE ev_memory_record SET content_sha256 = 'old-format'")
    out = reproject(conn, None, ev_settings, defer=True)
    assert out["deferred"] == 1 and len(past.batches()) == 1
    q = conn.execute("SELECT status, skip_reason FROM ev_proposition_queue").fetchone()
    assert (q["status"], q["skip_reason"]) == ("queued", "pastdev_pending")
