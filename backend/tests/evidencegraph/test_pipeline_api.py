import json

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from conftest import TEST_DATABASE_URL
from polgraph.pipeline_api import STAGE_KEYS, build_router
from test_enrich import FakePast, _enricher, _setup
from test_intake import PID, _claim


def _read_only(url):
    return psycopg.connect(url, row_factory=dict_row, options="-c default_transaction_read_only=on")


@pytest.fixture
def api(conn, ev_settings, tmp_path):
    _setup(conn)                                             # prop-homicidios: enriched and sent below
    _claim(conn, "b", "prop-opinion", claim_type="opinion")  # added after intake ran
    _claim(conn, "c", "prop-inferred", grounding="review")  # never eligible
    enricher, _ = _enricher(conn, ev_settings, tmp_path, FakePast())
    assert not enricher.run(None, 5)["errors"]
    conn.commit()
    export = tmp_path / "claims" / "MX"
    export.mkdir(parents=True)
    sent = conn.execute("SELECT claim_id FROM distilled_claim WHERE proposition_id = 'prop-homicidios'").fetchone()
    (export / "claims.jsonl").write_text(json.dumps({"claim_id": sent["claim_id"]}) + "\n" + json.dumps({"claim_id": "x"}) + "\n")
    (export / "manifest.json").write_text(json.dumps({"source": "past.dev", "pulled_at": "2026-11-15T00:00:00Z"}))
    app = FastAPI()
    app.include_router(build_router(lambda: TEST_DATABASE_URL, lambda: tmp_path / "claims", _read_only))
    return TestClient(app)


def test_summary_funnel_stages_and_reconciliation(api):
    s = api.get("/api/pipeline/mx/summary").json()
    assert s["available"] and s["country"] == "MX"
    funnel = {f["key"]: f["count"] for f in s["funnel"]}
    assert funnel["records"] == 3 and funnel["eligible"] == 2 and funnel["usable"] == 1 and funnel["sent"] == 1
    assert sum(s["stages"].values()) == 3 and set(s["stages"]) == set(STAGE_KEYS)
    pol = next(p for p in s["politicians"] if p["politician_id"] == PID)
    assert pol["records"] == 3 and pol["stages"]["waiting_for_intake"] == 1 and pol["stages"]["not_eligible"] == 1
    r = s["reconciliation"]
    assert (r["neon_sent_claims"], r["in_both"], r["sent_not_in_explorer"], r["in_explorer_not_in_log"]) == (1, 1, 0, 1)


def test_records_filter_and_trace(api):
    out = api.get("/api/pipeline/MX/records", params={"q": "homicidios"}).json()
    assert out["total"] == 1
    rec = out["records"][0]
    assert rec["stage"] in ("sent_unconfirmed", "in_pastdev") and rec["usable"] == 2 and rec["datapoint_id"].startswith("clm-")
    assert api.get("/api/pipeline/MX/records", params={"stage": "waiting_to_send"}).json()["total"] == 0
    assert api.get("/api/pipeline/MX/records", params={"stage": "nope"}).status_code == 422
    assert api.get("/api/pipeline/MX/records", params={"limit": 1000}).status_code == 422

    t = api.get(f"/api/pipeline/MX/trace/{rec['proposition_id']}", params={"politician_id": PID}).json()
    assert t["records"][0]["in_explorer"] is True and t["claims"] and t["queue"]["status"] == "mapped"
    assert t["searches"] and any(s["urls"] for s in t["searches"])
    reasons = {e["stance"]: e["not_sent_because"] for e in t["evidence"]}
    assert reasons["supports"] is None and reasons["contradicts"] == "quote was not found in the fetched page"
    assert {x["kind"] for x in t["sent"]} == {"claim"}  # evidence is folded into the claim record
    assert api.get("/api/pipeline/MX/trace/unknown").status_code == 404


def test_connection_is_read_only_and_unconfigured_is_reported(tmp_path):
    try:
        with _read_only(TEST_DATABASE_URL) as c, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            c.execute("CREATE TABLE pipeline_api_probe (x int)")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres test database unavailable: {exc}")
    app = FastAPI()
    app.include_router(build_router(lambda: None, lambda: tmp_path))
    assert TestClient(app).get("/api/pipeline/MX/summary").json() == {
        "available": False, "reason": "PIPELINE_DATABASE_URL is not set"}
