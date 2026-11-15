import math
from datetime import date, datetime, timedelta, timezone

import pytest

from evidencegraph.intake import run_intake

PID = "person:MX:claudia-sheinbaum-pardo"
OTHER_PID = "person:MX:clara-marina-brugada-molina"
RUN_ID = "ev-intake-test-run"


def _base_run(conn):
    conn.execute(
        "INSERT INTO run(run_id, politician_id, person_updated_at, politician_version, pipeline_mode, "
        "query_id, query_json, date_from, date_to, platforms, projection, max_posts) "
        "VALUES (%s,%s,%s,1,'research_only','q','{}',%s,%s,%s,'full',10)",
        (RUN_ID, PID, datetime.now(timezone.utc), date.today() - timedelta(days=30), date.today(),
         ["tiktok", "instagram"]),
    )


def _claim(conn, name, proposition_id, claim_type="statistic", cw=0.8, *, pid=PID, version="v1",
           grounding="exact", modality="asserted", period_end=None, views=10, extracted_at=None,
           oriane_id=None, raw_id=None):
    extracted_at = extracted_at or datetime.now(timezone.utc)
    oriane_id = oriane_id or f"post-{name}"
    raw_id = raw_id or f"raw-{name}-{version}"
    conn.execute(
        "INSERT INTO raw_data(raw_id,run_id,oriane_id,platform,projection,payload_sha256,pipeline_mode,"
        "published_at,captured_at) VALUES (%s,%s,%s,'tiktok','full','hash','research_only',%s,%s)",
        (raw_id, RUN_ID, oriane_id, extracted_at, extracted_at),
    )
    conn.execute(
        "INSERT INTO post(oriane_id,platform,pipeline_mode,latest_raw_id,published_at,first_observed_at,last_captured_at) "
        "VALUES (%s,'tiktok','research_only',%s,%s,%s,%s) ON CONFLICT (oriane_id) DO NOTHING",
        (oriane_id, raw_id, extracted_at, extracted_at, extracted_at),
    )
    conn.execute("UPDATE post SET latest_raw_id=%s,last_captured_at=%s WHERE oriane_id=%s",
                 (raw_id, extracted_at, oriane_id))
    conn.execute(
        "INSERT INTO claim_extraction(oriane_id,raw_id,extractor_version,politician_id,model,status,claims,"
        "extracted_at) VALUES (%s,%s,%s,%s,'test','ok',1,%s)",
        (oriane_id, raw_id, version, pid, extracted_at),
    )
    conn.execute(
        "INSERT INTO proposition(proposition_id,canonical_text,subject,predicate,negated,vals,place,period_start,"
        "period_end,qualifiers,topic_id,created_at,first_stated_at) "
        "VALUES (%s,%s,'subject','predicate',false,'[]',NULL,NULL,%s,'[]',NULL,%s,%s) "
        "ON CONFLICT (proposition_id) DO NOTHING",
        (proposition_id, proposition_id, period_end, extracted_at, extracted_at),
    )
    conn.execute(
        "INSERT INTO distilled_claim(claim_id,oriane_id,raw_id,politician_id,segment_ids,proposition_id,quote,text,"
        "speaker_status,relation_to_politician,type,topics,checkworthiness,modality,grounding,stated_at,"
        "extractor_version,extracted_at) VALUES (%s,%s,%s,%s,'{}',%s,'quote','claim','unknown','speaker',%s,"
        "'{seguridad}',%s,%s,%s,%s,%s,%s)",
        (f"claim-{name}-{version}", oriane_id, raw_id, pid, proposition_id, claim_type, cw, modality, grounding,
         extracted_at, version, extracted_at),
    )
    conn.execute(
        "INSERT INTO post_metrics(oriane_id,captured_at,views) VALUES (%s,%s,%s)",
        (oriane_id, extracted_at, views),
    )


def test_intake_eligibility_priority_latest_claims_and_idempotence(conn):
    _base_run(conn)
    old = datetime.now(timezone.utc) - timedelta(days=5)
    now = datetime.now(timezone.utc)
    _claim(conn, "latest", "prop-latest", version="old", cw=0.99, views=999, extracted_at=old)
    _claim(conn, "latest", "prop-latest", version="new", cw=0.8, views=10, extracted_at=now)
    conn.execute("INSERT INTO post_metrics(oriane_id,captured_at,views) VALUES ('post-latest',%s,100)",
                 (now + timedelta(seconds=1),))
    _claim(conn, "low", "prop-low", cw=0.3)
    _claim(conn, "review", "prop-review", grounding="review")
    _claim(conn, "failed", "prop-failed", grounding="failed")
    _claim(conn, "questioned", "prop-questioned", modality="questioned")
    _claim(conn, "satire", "prop-satire", modality="satire")
    _claim(conn, "opinion", "prop-opinion", claim_type="opinion")
    _claim(conn, "commit-future", "prop-future", claim_type="commitment", period_end=date.today() + timedelta(days=2))
    _claim(conn, "commit-null", "prop-null", claim_type="commitment")
    _claim(conn, "commit-past", "prop-past", claim_type="commitment", period_end=date.today() - timedelta(days=2))
    _claim(conn, "other-politician", "prop-other", pid=OTHER_PID)

    dry = run_intake(conn, [PID], min_cw=0.5, mapper_version="m0", dry_run=True)
    assert dry["status"]["queued"] == 2
    assert dry["skip_reason"] == {
        "below_checkworthiness": 1,
        "commitment_deadline_pending": 2,
        "not_currently_verifiable": 1,
    }
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue").fetchone()["n"] == 0

    result = run_intake(conn, [PID], min_cw=0.5, mapper_version="m0")
    latest = conn.execute("SELECT * FROM ev_proposition_queue WHERE proposition_id='prop-latest'").fetchone()
    assert latest["status"] == "queued"
    assert latest["max_checkworthiness"] == 0.8
    assert latest["reach"] == 100
    assert latest["priority"] == pytest.approx(0.8 * math.log(101))
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue WHERE proposition_id='prop-latest'").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue WHERE proposition_id LIKE 'prop-%' "
                        "AND proposition_id IN ('prop-review','prop-failed','prop-questioned','prop-satire')").fetchone()["n"] == 0
    assert conn.execute("SELECT status,skip_reason FROM ev_proposition_queue WHERE proposition_id='prop-low'").fetchone() == {
        "status": "skipped", "skip_reason": "below_checkworthiness",
    }
    assert conn.execute("SELECT status,skip_reason FROM ev_proposition_queue WHERE proposition_id='prop-opinion'").fetchone()["skip_reason"] == "not_currently_verifiable"
    assert conn.execute("SELECT status FROM ev_proposition_queue WHERE proposition_id='prop-past'").fetchone()["status"] == "queued"
    assert run_intake(conn, [PID], mapper_version="m0")["status"] == result["status"]
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue WHERE proposition_id='prop-other'").fetchone()["n"] == 0
    assert run_intake(conn, [OTHER_PID], mapper_version="m0")["status"]["queued"] == 1


def test_intake_uses_only_latest_raw_capture_for_same_extractor(conn):
    _base_run(conn)
    now = datetime.now(timezone.utc)
    _claim(conn, "old-capture", "prop-old-capture", version="checks-1", cw=0.99,
           oriane_id="same-post", raw_id="raw-old", extracted_at=now - timedelta(hours=1))
    _claim(conn, "new-capture", "prop-new-capture", version="checks-1", cw=0.8,
           oriane_id="same-post", raw_id="raw-new", extracted_at=now)

    result = run_intake(conn, [PID], mapper_version="m0")

    assert result["upserted"] == 1
    assert conn.execute("SELECT status,max_checkworthiness FROM ev_proposition_queue "
                        "WHERE proposition_id='prop-new-capture'").fetchone() == {
                            "status": "queued", "max_checkworthiness": 0.8,
                        }
    assert conn.execute("SELECT count(*) AS n FROM ev_proposition_queue "
                        "WHERE proposition_id='prop-old-capture'").fetchone()["n"] == 0


def test_intake_mapped_rows_and_stale_queue(conn):
    _base_run(conn)
    now = datetime.now(timezone.utc)
    _claim(conn, "mapped", "prop-mapped", extracted_at=now)
    conn.execute(
        "INSERT INTO ev_proposition_queue(proposition_id,politician_ids,claim_types,topics,max_checkworthiness,reach,"
        "priority,stated_at,latest_claim_at,claim_count,status,mapper_version,queued_at,mapped_at,updated_at) "
        "VALUES ('prop-stale',%s,'{statistic}','{}',0.8,0,0,%s,%s,1,'queued',NULL,%s,NULL,%s)",
        ([PID], now, now, now, now),
    )
    run_intake(conn, [PID], mapper_version="m0")
    conn.execute("UPDATE ev_proposition_queue SET status='mapped',mapper_version='m0',mapped_at=%s "
                 "WHERE proposition_id='prop-mapped'", (now + timedelta(days=1),))
    run_intake(conn, [PID], mapper_version="m0")
    assert conn.execute("SELECT status FROM ev_proposition_queue WHERE proposition_id='prop-mapped'").fetchone()["status"] == "mapped"
    assert conn.execute("SELECT status,skip_reason FROM ev_proposition_queue WHERE proposition_id='prop-stale'").fetchone() == {
        "status": "skipped", "skip_reason": "no_current_claims",
    }

    _claim(conn, "mapped-new", "prop-mapped", extracted_at=now + timedelta(days=2))
    run_intake(conn, [PID], mapper_version="m0")
    assert conn.execute("SELECT status FROM ev_proposition_queue WHERE proposition_id='prop-mapped'").fetchone()["status"] == "queued"
