from dataclasses import replace

import pytest

from system_d.config import Settings
from system_d.controller import parse_claim_selection, run_simulation
from system_d.llm import Usage
from system_d.models import Respondent
from system_d.stopping import Response


class FakeStore:
    def __init__(self, claim_ids=("claim-1",), strata=(("stratum-a", 0.5), ("stratum-b", 0.5))):
        self.claims = [
            {
                "id": claim_id,
                "canonical_text": f"Canonical text for {claim_id}",
                "politician_id": "politician-1",
                "politician_name": "Politician",
            }
            for claim_id in claim_ids
        ]
        self.strata = [{"id": stratum_id, "share": share} for stratum_id, share in strata]
        self.responses = {claim_id: [] for claim_id in claim_ids}
        self.states = {}
        self.batch_numbers = {claim_id: [] for claim_id in claim_ids}

    def get_testable_aggregated_claims(self, claim_ids=None, limit=None):
        selected = self.claims
        if claim_ids is not None:
            selected = [claim for claim in selected if claim["id"] in claim_ids]
        return selected[:limit] if limit is not None else selected

    def get_sampling_state(self, claim_id, sim_run):
        return self.states.get((claim_id, sim_run))

    def list_strata(self):
        return self.strata

    def latest_question_set(self, claim_id):
        return {"id": f"question-{claim_id}", "version": 1, "script": {}}

    def responses_for(self, claim_id, sim_run, question_set_id):
        return list(self.responses[claim_id])

    def get_social_sentiment(self, claim_id):
        return 0.5

    def upsert_sampling_state(self, claim_id, sim_run, stats, status):
        self.states[(claim_id, sim_run)] = {
            "status": status,
            "n_completed": stats.n_completed,
            "p_believe": stats.p_believe,
            "ci_low": stats.ci_low,
            "ci_high": stats.ci_high,
            "sentiment_mean": stats.sentiment_mean,
            "sentiment_ci_low": stats.sentiment_ci_low,
            "sentiment_ci_high": stats.sentiment_ci_high,
            "social_sentiment": stats.social_sentiment,
        }

    def max_batch_no(self, claim_id, sim_run):
        return max(self.batch_numbers[claim_id], default=0)

    def record_response(self, task, completed=True):
        stratum_id = next(
            respondent.stratum_id for respondent in self.respondents if respondent.id == task["respondent_id"]
        )
        index = len(self.responses[task["aggregated_claim_id"]])
        self.responses[task["aggregated_claim_id"]].append(
            Response(stratum_id, completed, index % 2 == 0, 0.5 if completed else None)
        )
        self.batch_numbers[task["aggregated_claim_id"]].append(task["batch_no"])


class FakeLLM:
    pass


def empty_usage():
    return Usage("mixed", 0, 0)


def make_sampler(store, respondents_per_stratum=None):
    counter = 0
    respondents_per_stratum = respondents_per_stratum or {}

    def sample(store_arg, llm, sim_run, claim_id, allocation, settings):
        nonlocal counter
        selected = []
        store_arg.respondents = getattr(store_arg, "respondents", [])
        for stratum_id, count in allocation.items():
            already = respondents_per_stratum.get(stratum_id, 0)
            for _ in range(count):
                counter += 1
                respondent = Respondent(
                    id=f"respondent-{counter}",
                    stratum_id=stratum_id,
                    persona={"sim_run": sim_run},
                    channel="simulator",
                )
                selected.append(respondent)
                store_arg.respondents.append(respondent)
                respondents_per_stratum[stratum_id] = already + 1
        return selected

    return sample


def test_parse_claim_selection_supports_all_count_and_ids():
    assert parse_claim_selection("all") == (None, None)
    assert parse_claim_selection("12") == (None, 12)
    assert parse_claim_selection("00000000-0000-0000-0000-000000000001, 00000000-0000-0000-0000-000000000002") == (
        ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"],
        None,
    )
    with pytest.raises(ValueError, match="positive"):
        parse_claim_selection("0")
    with pytest.raises(ValueError, match="comma-separated"):
        parse_claim_selection("one,,two")


def test_controller_runs_polling_rounds_until_every_claim_is_terminal():
    store = FakeStore()
    settings = replace(
        Settings(),
        seed_batch=10,
        next_batch=10,
        min_per_stratum=2,
        max_calls=100,
        target_half_width=0.4,
        max_usd=1.0,
    )
    events = []

    def prepare():
        events.append("prepared")
        return Usage("claude-sonnet-5-5", 10_000, 10_000)

    def poll_batch(tasks):
        events.append(len(tasks))
        results = []
        for task in tasks:
            store.record_response(task)
            results.append({"ok": True, "usage_usd": 0.001, "completed": True, "input_tokens": 10, "output_tokens": 4})
        return results

    summaries = run_simulation(
        store,
        "run-1",
        None,
        settings,
        poll_batch,
        prepare_fn=prepare,
        log=events.append,
        sample_fn=make_sampler(store),
        llm=FakeLLM(),
    )
    assert events[0] == "prepared"
    assert events[1] == 10
    assert len(summaries) == 1
    assert summaries[0].status == "converged"
    assert summaries[0].n == 10
    assert summaries[0].prepare_spend_usd == pytest.approx(0.12)
    assert summaries[0].persona_rewrite_spend_usd == 0
    assert summaries[0].poll_spend_usd == pytest.approx(0.01)
    assert summaries[0].total_spend_usd == pytest.approx(0.13)
    assert summaries[0].input_tokens == 10_100
    assert summaries[0].output_tokens == 10_040
    assert any(
        isinstance(message, str) and "prepare=$0.1200" in message and "polls=$0.0100" in message for message in events
    )


def test_controller_obeys_spend_cap_and_leaves_claim_polling():
    store = FakeStore()
    settings = replace(Settings(), seed_batch=10, min_per_stratum=2, max_usd=0.005)
    dispatched = []
    logs = []
    summaries = run_simulation(
        store,
        "run-1",
        None,
        settings,
        poll_batch_fn=lambda tasks: dispatched.extend(tasks) or [],
        prepare_fn=empty_usage,
        log=logs.append,
        sample_fn=make_sampler(store),
        llm=FakeLLM(),
    )
    assert dispatched == []
    assert summaries[0].status == "polling"
    assert store.states[("claim-1", "run-1")]["status"] == "polling"
    assert any("SPEND CAP" in message for message in logs)


def test_controller_resume_skips_terminal_claims():
    store = FakeStore()
    store.states[("claim-1", "run-1")] = {
        "status": "converged",
        "n_completed": 44,
        "p_believe": 0.6,
        "ci_low": 0.5,
        "ci_high": 0.7,
        "sentiment_mean": 0.2,
        "sentiment_ci_low": 0.1,
        "sentiment_ci_high": 0.3,
        "social_sentiment": 0.0,
    }
    calls = []
    summaries = run_simulation(
        store,
        "run-1",
        None,
        Settings(),
        poll_batch_fn=lambda tasks: calls.append("poll") or [],
        prepare_fn=lambda: (calls.append("prepare"), empty_usage())[1],
        log=lambda _: None,
        sample_fn=lambda *args: calls.append("sample") or [],
        llm=FakeLLM(),
    )
    assert calls == ["prepare"]
    assert summaries[0].status == "converged"
    assert summaries[0].n == 44


def test_capped_claim_queues_rewrite_and_is_not_repolled():
    store = FakeStore(strata=(("stratum-a", 1.0),))
    settings = replace(Settings(), seed_batch=1, max_calls=1, min_per_stratum=0, max_usd=1)
    dispatches = []
    rewrites = []

    def poll_batch(tasks):
        dispatches.append(tasks)
        store.record_response(tasks[0])
        return [{"ok": True, "usage_usd": 0.001, "completed": True}]

    summaries = run_simulation(
        store,
        "run-1",
        None,
        settings,
        poll_batch,
        prepare_fn=empty_usage,
        log=lambda _: None,
        sample_fn=make_sampler(store),
        rewrite_fn=lambda store_arg, llm, claim_id: rewrites.append(claim_id),
        llm=FakeLLM(),
    )
    assert len(dispatches) == 1
    assert rewrites == ["claim-1"]
    assert summaries[0].status == "capped"


def test_failed_capped_rewrite_does_not_persist_terminal_state():
    store = FakeStore(strata=(("stratum-a", 1.0),))
    settings = replace(Settings(), seed_batch=1, max_calls=1, min_per_stratum=0, max_usd=1)

    def poll_batch(tasks):
        store.record_response(tasks[0])
        return [{"ok": True, "usage_usd": 0.001, "completed": True}]

    def fail_rewrite(*args):
        raise RuntimeError("rewrite failed")

    with pytest.raises(RuntimeError, match="rewrite failed"):
        run_simulation(
            store,
            "run-1",
            None,
            settings,
            poll_batch,
            prepare_fn=empty_usage,
            log=lambda _: None,
            sample_fn=make_sampler(store),
            rewrite_fn=fail_rewrite,
            llm=FakeLLM(),
        )

    assert store.states[("claim-1", "run-1")]["status"] == "polling"


def test_failed_poll_tasks_are_not_counted_as_attempts():
    store = FakeStore()
    settings = replace(Settings(), seed_batch=2, max_calls=20, max_usd=1)
    summaries = run_simulation(
        store,
        "run-1",
        None,
        settings,
        poll_batch_fn=lambda tasks: [RuntimeError("failed") for _ in tasks],
        prepare_fn=empty_usage,
        log=lambda _: None,
        sample_fn=make_sampler(store),
        llm=FakeLLM(),
    )
    assert summaries[0].status == "polling"
    assert summaries[0].n == 0
    assert store.responses["claim-1"] == []


def test_persona_generation_cost_alone_stops_poll_dispatch():
    store = FakeStore()
    settings = replace(Settings(), seed_batch=10, min_per_stratum=2, max_usd=1.0)
    dispatched = []
    logs = []

    class ExpensiveLLM:
        def chat(self, system, messages, max_tokens):
            return "persona generated", Usage("claude-sonnet-5-5", 1_000_000, 0)

    base_sampler = make_sampler(store)

    def expensive_sampler(store_arg, llm, sim_run, claim_id, allocation, config):
        llm.chat("system", [], 1)
        return base_sampler(store_arg, llm, sim_run, claim_id, allocation, config)

    summaries = run_simulation(
        store,
        "run-1",
        None,
        settings,
        poll_batch_fn=lambda tasks: dispatched.extend(tasks) or [],
        prepare_fn=empty_usage,
        log=logs.append,
        sample_fn=expensive_sampler,
        llm=ExpensiveLLM(),
    )

    assert dispatched == []
    assert summaries[0].status == "polling"
    assert summaries[0].prepare_spend_usd == 0
    assert summaries[0].persona_rewrite_spend_usd == pytest.approx(2.0)
    assert summaries[0].poll_spend_usd == 0
    assert summaries[0].total_spend_usd == pytest.approx(2.0)
    assert summaries[0].input_tokens == 1_000_000
    assert any("personas+rewrites=$2.0000" in message for message in logs)
