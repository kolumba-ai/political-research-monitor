from datetime import UTC, datetime
from decimal import Decimal

from system_d.export_opinion import build


def _state(run, status, minute, **kw):
    return {
        "storyline_id": "sl-1",
        "storyline_label": "Label",
        "canonical_text": "Claim",
        "person_id": "person:MX:x",
        "sim_run": run,
        "status": status,
        "n_completed": 80,
        "p_believe": 0.4,
        "ci_low": 0.3,
        "ci_high": 0.5,
        "sentiment_mean": 0.1,
        "sentiment_ci_low": 0.0,
        "sentiment_ci_high": 0.2,
        "social_sentiment": 0.3,
        "updated_at": datetime(2026, 11, 15, 21, minute, tzinfo=UTC),
        "aggregated_claim_id": "a1",
        "social_comments": 12,
        **kw,
    }


def test_latest_is_the_most_recent_finished_run_with_strata_and_gap():
    states = [_state("run-1", "decisive", 1), _state("run-2", "polling", 5), _state("run-3", "converged", 3)]
    strata = [
        {
            "aggregated_claim_id": "a1",
            "sim_run": "run-3",
            "region": "Centro",
            "age_band": "18-29",
            "gender": "mujer",
            "n": 7,
            "share_believe": Decimal("0.5"),
            "sentiment_mean": Decimal("0.25"),
        }
    ]
    (entry,) = build(states, strata).values()
    assert entry["latest"] == "run-3" and set(entry["runs"]) == {"run-1", "run-2", "run-3"}
    assert entry["runs"]["run-3"]["gap"] == -0.2 and entry["runs"]["run-3"]["strata"][0]["share_believe"] == 0.5
    assert entry["runs"]["run-1"]["strata"] == [] and entry["politician_id"] == "person:MX:x"


def test_latest_falls_back_to_a_run_still_polling():
    (entry,) = build([_state("run-1", "polling", 1, social_sentiment=None)], []).values()
    assert entry["latest"] == "run-1" and entry["runs"]["run-1"]["gap"] is None
