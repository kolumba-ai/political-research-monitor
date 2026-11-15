"""past.dev claim writer: data point shape (readable by pull-pastdev), audiences, batching, skip-unchanged, spend gate."""
from __future__ import annotations

import json

import httpx

from socialclaims.pastdev import PastDevClient, parse_payload
from socialclaims.pastdev_push import audience_slug, collect, push

from .test_claims import PID, claim, extract_claims

QUOTE = "En el tercer trimestre el desempleo bajó a 2.5 por ciento"


def _claims(conn, graph, sc_settings):
    out, rows = extract_claims(conn, graph, sc_settings, QUOTE,
                               lambda r: [claim("el desempleo bajó a 2.5 por ciento", [r])])
    assert rows
    return rows


class FakePast:
    def __init__(self, identities=None, audiences=None, sources=None, ingest_status="completed"):
        self.calls, self.batches = [], []
        self.sources, self.ingest_status = sources or [], ingest_status
        self.identities = identities or []
        self.audiences = audiences or [{"slug": "project", "kind": "default", "identities": None}]

    def client(self):
        return PastDevClient("past_sk_test", transport=httpx.MockTransport(self.handle), sleep=lambda s: None)

    def handle(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer past_sk_test"
        path = req.url.path.removeprefix("/api/v1")
        self.calls.append((req.method, path))
        body = json.loads(req.content) if req.content else None
        if req.method == "GET" and path == "/identities":
            return httpx.Response(200, json={"identities": self.identities, "hasMore": False})
        if req.method == "GET" and path == "/audiences":
            return httpx.Response(200, json={"audiences": self.audiences})
        if req.method == "PUT" and path.startswith("/identities/"):
            self.identities.append({"identity": path.rsplit("/", 1)[1], "traits": body["traits"]})
            return httpx.Response(200, json={})
        if req.method == "PUT" and path.startswith("/audiences/"):
            self.audiences.append({"slug": path.rsplit("/", 1)[1], **body, "identities": None})
            return httpx.Response(201, json={})
        if req.method == "POST" and path == "/ingest/batch":
            self.batches.append(body)
            n = len(self.batches)
            return httpx.Response(202, json={"ingestionId": f"ing-{n}", "status": "queued", "unchanged": False,
                                             "items": [{"ordinal": i, "status": "queued", "unchanged": False}
                                                       for i in range(len(body["items"]))]})
        if req.method == "GET" and path == "/trace/roots":
            return httpx.Response(200, json={"sources": {"entries": self.sources, "hasMore": False}})
        if req.method == "GET" and path.startswith("/ingest/"):
            failure = {"message": "boom"} if self.ingest_status == "failed" else None
            return httpx.Response(200, json={"status": self.ingest_status, "failure": failure})
        return httpx.Response(404, json={"error": path})


def test_data_point_is_readable_by_pull(conn, graph, sc_settings):
    rows = _claims(conn, graph, sc_settings)
    [item] = collect(conn, "MX")
    assert item["id"] == f"sc-{rows[0]['oriane_id']}-claudia-sheinbaum-pardo"
    assert item["audience"] == "pol-claudia-sheinbaum-pardo"
    assert item["timestamp"].endswith("Z")
    assert item["content"].startswith("tiktok post by @claudiasheinbaum on ")
    payload = parse_payload(item["content"])
    assert payload["politician"]["system_a_person_id"] == PID
    assert [c["claim_id"] for c in payload["claims"]] == [r["claim_id"] for r in rows]
    assert payload["claims"][0]["speaker"] == "politician"
    assert item["metadata"]["claim_ids"] == [r["claim_id"] for r in rows]


def test_failed_grounding_is_not_sent(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    conn.execute("UPDATE distilled_claim SET grounding = 'failed'")
    assert collect(conn, "MX") == []


def test_dry_run_makes_no_call_and_estimates_credits(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    out = push(conn, None, "MX", dry_run=True)
    assert out["to_send"] == 1 and out["estimated_credits"] >= 1 and out["dry_run"]
    assert conn.execute("SELECT count(*) AS n FROM memory_record").fetchone()["n"] == 0


def test_push_creates_audience_records_and_skips_unchanged(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    past = FakePast()
    out = push(conn, past.client(), "MX", sleep=lambda s: None)
    assert out["sent"] == 1 and out["errors"] == [] and out["ingestions"] == {"ing-1": {"status": "completed",
                                                                                        "failure": None}}
    assert ("PUT", "/identities/reader-pol-claudia-sheinbaum-pardo") in past.calls
    aud = next(a for a in past.audiences if a["slug"] == "pol-claudia-sheinbaum-pardo")
    assert aud["rule"]["conditions"][0] == {"trait": "politician", "operator": "is", "values": [PID]}
    [batch] = past.batches
    assert batch["idempotencyKey"].startswith("sc-push-") and batch["items"][0]["audience"] == aud["slug"]
    rec = conn.execute("SELECT * FROM memory_record").fetchone()
    assert rec["status"] == "completed" and rec["ingestion_id"] == "ing-1" and rec["past_project"] == "claims-mx"

    again = push(conn, past.client(), "MX", sleep=lambda s: None)
    assert again["to_send"] == 0 and again["already_sent"] == 1 and len(past.batches) == 1

    conn.execute("UPDATE distilled_claim SET text_en = 'changed'")
    third = push(conn, past.client(), "MX", sleep=lambda s: None)
    assert third["sent"] == 1 and len(past.batches) == 2
    assert past.batches[1]["items"][0]["id"] == batch["items"][0]["id"]


def test_existing_reader_audience_is_reused(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    past = FakePast(identities=[{"identity": "reader-pol-claudia", "traits": {"politician": PID}}],
                    audiences=[{"slug": "pol-claudia", "kind": "fixed", "identities": ["reader-pol-claudia"]}])
    push(conn, past.client(), "MX", sleep=lambda s: None)
    assert not [c for c in past.calls if c[0] == "PUT"]
    assert past.batches[0]["items"][0]["audience"] == "pol-claudia"


def test_max_credits_refuses_before_sending(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    past = FakePast()
    out = push(conn, past.client(), "MX", max_credits=1, sleep=lambda s: None)
    assert out["estimated_credits"] > 1 and "exceeds" in out["error"]
    assert past.batches == [] and conn.execute("SELECT count(*) AS n FROM memory_record").fetchone()["n"] == 0


def test_shared_audience_is_not_reused(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    past = FakePast(identities=[{"identity": "reader-pol-claudia", "traits": {"politician": PID}},
                                {"identity": "someone-else", "traits": {"politician": "person:MX:otro"}}],
                    audiences=[{"slug": "shared", "kind": "fixed", "identities": ["reader-pol-claudia", "someone-else"]}])
    push(conn, past.client(), "MX", sleep=lambda s: None)
    assert past.batches[0]["items"][0]["audience"] == "pol-claudia-sheinbaum-pardo"


def _legacy(oriane_id, name):
    return {"id": "n1", "kind": "source", "dataPointId": f"oriane:{oriane_id}:v1", "current": True,
            "headline": f"tiktok post by @x on 2026-11-11 mentioning {name}. 1 claim(s):"}


def test_posts_already_written_by_earlier_pipeline_are_skipped(conn, graph, sc_settings):
    rows = _claims(conn, graph, sc_settings)
    for name in ("Claudia Sheinbaum Pardo", "Nadie Conocido"):
        past = FakePast(sources=[_legacy(rows[0]["oriane_id"], name)])
        out = push(conn, past.client(), "MX", graph=graph, sleep=lambda s: None)
        assert out["skipped_legacy"] == 1 and out["to_send"] == 0 and past.batches == []
    past = FakePast(sources=[_legacy(rows[0]["oriane_id"], "Claudia Sheinbaum Pardo")])
    assert push(conn, past.client(), "MX", graph=graph, skip_legacy=False, sleep=lambda s: None)["sent"] == 1


def test_dry_run_with_client_only_reads(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    past = FakePast()
    out = push(conn, past.client(), "MX", graph=graph, dry_run=True)
    assert out["to_send"] == 1 and out["audiences_to_create"] == ["pol-claudia-sheinbaum-pardo"]
    assert all(m == "GET" for m, _ in past.calls)
    assert conn.execute("SELECT count(*) AS n FROM memory_record").fetchone()["n"] == 0


def test_unfinished_ingestion_is_rechecked_and_resent_when_failed(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    past = FakePast(ingest_status="processing")
    push(conn, past.client(), "MX", poll_timeout_s=0, sleep=lambda s: None)
    assert conn.execute("SELECT status FROM memory_record").fetchone()["status"] == "queued"
    assert push(conn, past.client(), "MX", poll_timeout_s=0, sleep=lambda s: None)["to_send"] == 0
    past.ingest_status = "failed"
    out = push(conn, past.client(), "MX", sleep=lambda s: None)
    assert out["queued_rechecked"] == 1 and out["sent"] == 1 and len(past.batches) == 2


def test_filtered_push_does_not_report_other_politicians_stale(conn, graph, sc_settings):
    _claims(conn, graph, sc_settings)
    push(conn, FakePast().client(), "MX", sleep=lambda s: None)
    out = push(conn, None, "MX", politician_ids=["person:MX:otro"], dry_run=True)
    assert out["stale_records"] == []


def test_audience_slug_fits_limit():
    long = "person:MX:maria-de-los-angeles-gutierrez-hernandez-de-la-garza"
    slug = audience_slug(long)
    assert len(slug) <= 40 and slug.startswith("pol-maria") and slug == audience_slug(long)
    assert audience_slug(PID) == "pol-claudia-sheinbaum-pardo"


def test_cli_refuses_without_confirm_spend(monkeypatch, capsys):
    from socialclaims import cli
    assert cli.main(["push-pastdev"]) == 2
    assert "--confirm-spend" in capsys.readouterr().err
