import json
from datetime import timedelta
from pathlib import Path

import pytest

from socialclaims import accounts
from socialclaims.capture import Capturer, NotTrackable, purge_expired, sha256_json
from socialclaims.oriane import credits_for
from socialclaims.query import count_filter_values, request_body
from socialclaims.relevance import score

from .conftest import FIXED_NOW, FakeOriane, make_result

PID = "person:MX:claudia-sheinbaum-pardo"


def cap(conn, graph, settings, fake, now=FIXED_NOW):
    return Capturer(conn, graph, fake.client() if fake else None, settings, clock=lambda: now)


def test_credit_math():
    assert credits_for(100, "full") == 400 and credits_for(10, "default") == 30 and credits_for(3, "basic") == 6
    assert credits_for(0, "full") == 0


def test_request_body_shape_and_exclude_budget():
    q = {"platforms": ["instagram", "tiktok"], "branches": [
        {"name": "written", "operator": "or", "filters": {"caption": {"includesExactly": {"values": ["a b"] * 3}}}}]}
    body, n = request_body(q, "2026-08-16", "2026-11-14", [f"id{i}" for i in range(1000)])
    assert body["operator"] == "and" and body["queries"][0]["operator"] == "or"
    assert body["filters"]["publishedAt"] == {"after": "2026-08-16", "before": "2026-11-14"}
    assert count_filter_values(body) == 500 and n == 500 - 2 - 2 - 3
    with pytest.raises(ValueError):
        request_body({"platforms": ["tiktok"], "branches": []}, "a", "b")


def test_capture_pages_and_persists(conn, graph, sc_settings):
    results = [make_result(i, matched=("written", "spoken") if i % 2 else ("onscreen",)) for i in range(250)]
    fake = FakeOriane(results)
    out = cap(conn, graph, sc_settings, fake).capture(PID, max_posts=230)
    assert out["status"] == "ok" and out["rows"] == 230 and out["credits"] == 920
    assert [c["params"]["limit"] for c in fake.calls] == ["100", "100", "30"]
    assert [c["params"]["offset"] for c in fake.calls] == ["0", "100", "200"]
    assert all(c["params"]["projection"] == "full" and c["params"]["sort"] == "publishedAt:desc" for c in fake.calls)
    assert fake.calls[0]["auth"] == "Bearer test-key"
    assert conn.execute("SELECT count(*) c FROM post").fetchone()["c"] == 230
    assert conn.execute("SELECT sum(credits) s FROM oriane_usage").fetchone()["s"] == 920
    run = conn.execute("SELECT * FROM run").fetchone()
    assert run["politician_version"] == 1 and run["pipeline_mode"] == "research_only"
    assert run["graph_run"] == "run-MX-20261114T230338Z" and len(run["request_ids"]) == 3
    assert run["branch_counts"]["written"] == 115 and run["total_count"] == 250
    assert len(run["query_id"]) == 64 and run["query_json"]["politician_id"] == PID


def test_raw_snapshot_is_exact_and_hashed(conn, graph, sc_settings):
    fake = FakeOriane([make_result(1)])
    cap(conn, graph, sc_settings, fake).capture(PID)
    raw = conn.execute("SELECT * FROM raw_data").fetchone()
    path, pointer = raw["payload_path"].split("#")
    f = Path(sc_settings.raw_storage_dir) / path
    assert oct(f.stat().st_mode & 0o777) == "0o600"
    env = json.loads(f.read_text())
    assert env["request"]["body"] == fake.calls[0]["body"]
    assert env["request"]["params"]["limit"] == 100
    result = env["response"]["data"]["results"][int(pointer.rsplit("/", 1)[1])]
    assert sha256_json(result) == raw["payload_sha256"]
    assert "profileEmails" not in result and raw["excluded_fields"] == ["profileEmails"]
    assert env["response"]["metadata"]["requestId"] == raw["request_id"]
    assert raw["published_at"].isoformat() == "2026-11-10T12:00:00+00:00" and raw["captured_at"] == FIXED_NOW
    page = conn.execute("SELECT manifest FROM run").fetchone()["manifest"]["pages"][0]
    assert page["sha256"] == sha256_json(env)


def test_incremental_capture_uses_id_excludes(conn, graph, sc_settings):
    first = [make_result(i) for i in range(5)]
    cap(conn, graph, sc_settings, FakeOriane(first)).capture(PID)
    fake = FakeOriane(first + [make_result(i) for i in range(5, 8)])
    later = FIXED_NOW + timedelta(days=1)
    out = cap(conn, graph, sc_settings, fake, now=later).capture(PID)
    assert sorted(fake.calls[0]["body"]["filters"]["id"]["excludes"]) == sorted(r["id"] for r in first)
    assert out["rows"] == 3 and out["excluded_ids"] == 5
    assert conn.execute("SELECT count(*) c FROM post").fetchone()["c"] == 8


def test_recapture_keeps_first_observed_and_appends_metrics(conn, graph, sc_settings):
    cap(conn, graph, sc_settings, FakeOriane([make_result(1, matched=("written",))])).capture(PID)
    # Another politician's run re-captures the same post with a new branch and new metrics.
    later = FIXED_NOW + timedelta(days=2)
    fake = FakeOriane([make_result(1, matched=("spoken",), viewsCount=5000)])
    cap(conn, graph, sc_settings, fake, now=later).capture("person:MX:clara-marina-brugada-molina")
    post = conn.execute("SELECT * FROM post").fetchone()
    assert post["first_observed_at"] == FIXED_NOW and post["last_captured_at"] == later
    assert post["matched_queries"] == ["spoken", "written"]
    views = [r["views"] for r in conn.execute("SELECT views FROM post_metrics ORDER BY captured_at").fetchall()]
    assert views == [1001, 5000]
    assert conn.execute("SELECT count(*) c FROM run_post").fetchone()["c"] == 2


def test_status_handling(conn, graph, sc_settings):
    out = cap(conn, graph, sc_settings, FakeOriane([make_result(i) for i in range(150)], fail={1: 402})).capture(PID)
    assert out["status"] == "credits_exhausted" and out["rows"] == 100
    assert conn.execute("SELECT count(*) c FROM post").fetchone()["c"] == 100   # first page persisted

    out = cap(conn, graph, sc_settings, FakeOriane([make_result(i) for i in range(200, 300)])).capture(PID)
    assert out["status"] == "ok" and out["rows"] == 100        # exact page → second call 416 ends cleanly

    out = cap(conn, graph, sc_settings, FakeOriane([], fail={0: 400})).capture(PID)
    assert out["status"] == "invalid_query" and "400" in out["error"]

    out = cap(conn, graph, sc_settings, FakeOriane([make_result(900)], partial=True)).capture(PID)
    assert out["status"] == "partial" and out["rows"] == 1

    fake = FakeOriane([make_result(950)], fail={0: 503, 1: 500})
    out = cap(conn, graph, sc_settings, fake).capture(PID)
    assert out["status"] == "ok" and len(fake.calls) == 3      # retried server errors


def test_monthly_and_run_ceilings(conn, graph, sc_settings):
    sc_settings.oriane_monthly_credit_ceiling = 500
    fake = FakeOriane([make_result(i) for i in range(300)])
    out = cap(conn, graph, sc_settings, fake).capture(PID)
    assert out["status"] == "budget_ceiling" and out["credits"] <= 500
    assert [c["params"]["limit"] for c in fake.calls] == ["100", "25"]
    fake2 = FakeOriane([make_result(i) for i in range(400, 500)])
    out2 = cap(conn, graph, sc_settings, fake2).capture(PID)
    assert out2["status"] == "budget_ceiling" and fake2.calls == []

    sc_settings.oriane_monthly_credit_ceiling = 100_000
    sc_settings.oriane_run_credit_ceiling = 200
    fake3 = FakeOriane([make_result(i) for i in range(600, 800)])
    out3 = cap(conn, graph, sc_settings, fake3).capture(PID)
    assert out3["credits"] == 200 and out3["status"] == "budget_ceiling"


def test_ineligible_and_review_politicians_are_refused(conn, graph, sc_settings):
    c = cap(conn, graph, sc_settings, FakeOriane([]))
    with pytest.raises(NotTrackable):
        c.capture("person:MX:former-official")
    with pytest.raises(NotTrackable):
        c.capture("person:MX:alfredo-ramirez-bedolla")
    with pytest.raises(NotTrackable):
        c.capture("person:MX:does-not-exist")
    assert c.capture("person:MX:alfredo-ramirez-bedolla", allow_review=True)["status"] == "ok"


def test_missing_published_at_goes_to_review(conn, graph, sc_settings):
    out = cap(conn, graph, sc_settings, FakeOriane([make_result(1, publishedAt=None), make_result(2)])).capture(PID)
    assert out["rows"] == 2
    assert conn.execute("SELECT count(*) c FROM post").fetchone()["c"] == 1
    assert conn.execute("SELECT reason FROM review").fetchone()["reason"] == "missing_published_at"


def test_dry_run_plan_makes_no_calls(conn, graph, sc_settings):
    plan = cap(conn, graph, sc_settings, None).plan(PID, date_to="2026-11-14")
    assert plan["date_from"] == "2026-08-16" and plan["worst_case_credits"] == 4000
    assert conn.execute("SELECT count(*) c FROM oriane_usage").fetchone()["c"] == 0


def test_verified_handle_adds_own_branch(conn, graph, sc_settings):
    accounts.approve(conn, PID, "tiktok", "claudiashein", "reviewer", ["https://www.gob.mx/presidencia"])
    plan = cap(conn, graph, sc_settings, None).plan(PID)
    names = [q["name"] for q in plan["body"]["queries"][0]["queries"]]
    assert names[:3] == ["own", "coauthored", "tagged"]


def test_relevance_scoring():
    ctx = {"full_names": ["claudia sheinbaum"], "entities": ["morena"], "languages": ["es"], "country_code": "MX"}
    own, _ = score(make_result(1), ["own"], ctx)
    spoken, basis = score(make_result(2, caption="", media=[{"transcript": "algo", "transcriptLanguage": "es"}],
                                      locationCountryCode="AR", locationCountryConfidence=1.0), ["spoken"], ctx)
    assert own > 0.9 and spoken < 0.4 and basis["full_name_hit"] is False
    multi, b = score(make_result(3), ["written", "onscreen"], ctx)
    assert multi > score(make_result(3), ["written"], ctx)[0] and b["entities"] == ["morena"]


def test_purge_expired(conn, graph, sc_settings):
    sc_settings.raw_retention_days = 30
    cap(conn, graph, sc_settings, FakeOriane([make_result(1)])).capture(PID)
    raw = conn.execute("SELECT payload_path FROM raw_data").fetchone()
    f = Path(sc_settings.raw_storage_dir) / raw["payload_path"].split("#")[0]
    assert purge_expired(conn, sc_settings, FIXED_NOW + timedelta(days=10)) == 0 and f.exists()
    assert purge_expired(conn, sc_settings, FIXED_NOW + timedelta(days=31)) == 1 and not f.exists()
    assert conn.execute("SELECT payload_sha256 FROM raw_data").fetchone()["payload_sha256"]


def test_relevance_reads_list_text_fields():
    post = make_result(1, caption=None, media=[{"type": "image", "transcript": None,
                                                 "onScreenText": ["Claudia Sheinbaum Pardo", "Morena"]}])
    value, basis = score(post, ["onscreen"], {"full_names": ["claudia sheinbaum pardo"], "entities": ["morena"]})
    assert basis["full_name_hit"] and value > 0.45


def test_spend_survives_processing_failure_and_replay_bills_once(conn, graph, sc_settings, monkeypatch):
    import socialclaims.capture as capmod
    from socialclaims.capture import RawReplayClient, month_credits_used
    fake = FakeOriane([make_result(i) for i in range(5)])

    def boom(*a, **k):
        raise TypeError("bad field shape")
    monkeypatch.setattr(capmod, "score", boom)
    with pytest.raises(TypeError):
        cap(conn, graph, sc_settings, fake).capture(PID)
    billed = credits_for(5, sc_settings.oriane_projection)
    assert month_credits_used(conn, FIXED_NOW) == billed
    run = conn.execute("SELECT run_id, status FROM run").fetchone()
    assert run["status"] == "error" and conn.execute("SELECT count(*) AS n FROM post").fetchone()["n"] == 0

    monkeypatch.undo()
    rc = RawReplayClient(Path(sc_settings.raw_storage_dir) / run["run_id"])
    w = rc.body["filters"]["publishedAt"]
    replayer = Capturer(conn, graph, rc, sc_settings, clock=lambda: rc.captured_at)
    out = replayer.capture(PID, date_from=w["after"], date_to=w["before"], max_posts=rc.max_posts)
    assert out["status"] == "ok" and out["rows"] == 5 and len(fake.calls) == 1
    assert conn.execute("SELECT count(*) AS n FROM post").fetchone()["n"] == 5
    assert month_credits_used(conn, FIXED_NOW) == billed
    with pytest.raises(ValueError):
        Capturer(conn, graph, rc, sc_settings, clock=lambda: rc.captured_at).capture(
            PID, date_from="2026-01-01", date_to=w["before"], max_posts=rc.max_posts)
