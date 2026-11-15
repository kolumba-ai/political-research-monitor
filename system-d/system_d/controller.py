"""Resumable, budget-limited polling controller."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .agents.sampling import select_respondents
from .config import Settings
from .db import Store
from .llm import LLM, MeteredLLM, Usage, UsageMeter, make_llm
from .models import Respondent
from .stopping import TERMINAL_STATUSES, Decision, Stats, Stratum, decide

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimSummary:
    aggregated_claim_id: str
    canonical_text: str
    status: str
    n: int
    p_believe: float | None
    ci_low: float | None
    ci_high: float | None
    sentiment_mean: float | None
    sentiment_ci_low: float | None
    sentiment_ci_high: float | None
    social_sentiment: float | None
    gap: float | None
    prepare_spend_usd: float
    persona_rewrite_spend_usd: float
    poll_spend_usd: float
    total_spend_usd: float
    input_tokens: int
    output_tokens: int


def parse_claim_selection(value: str) -> tuple[list[str] | None, int | None]:
    selection = value.strip()
    if selection.casefold() == "all":
        return None, None
    if selection.isdecimal():
        count = int(selection)
        if count < 1:
            raise ValueError("claim count must be positive")
        return None, count
    claim_ids = [claim_id.strip() for claim_id in selection.split(",")]
    if not claim_ids or any(not claim_id for claim_id in claim_ids):
        raise ValueError("claims must be 'all', a positive count, or comma-separated UUIDs")
    try:
        return [str(uuid.UUID(claim_id)) for claim_id in claim_ids], None
    except ValueError as exc:
        raise ValueError("claims must be 'all', a positive count, or comma-separated UUIDs") from exc


def _write_log(log: Callable[[str], None] | None, message: str) -> None:
    if log is None:
        logger.info(message)
    else:
        log(message)


def _stats_values(stats: Stats | dict[str, Any]) -> dict[str, Any]:
    if isinstance(stats, dict):
        return {
            "n": stats.get("n_completed", 0),
            "p_believe": stats.get("p_believe"),
            "ci_low": stats.get("ci_low"),
            "ci_high": stats.get("ci_high"),
            "sentiment_mean": stats.get("sentiment_mean"),
            "sentiment_ci_low": stats.get("sentiment_ci_low"),
            "sentiment_ci_high": stats.get("sentiment_ci_high"),
            "social_sentiment": stats.get("social_sentiment"),
        }
    return {
        "n": stats.n_completed,
        "p_believe": stats.p_believe,
        "ci_low": stats.ci_low,
        "ci_high": stats.ci_high,
        "sentiment_mean": stats.sentiment_mean,
        "sentiment_ci_low": stats.sentiment_ci_low,
        "sentiment_ci_high": stats.sentiment_ci_high,
        "social_sentiment": stats.social_sentiment,
    }


def _summary(
    claim: dict[str, Any],
    state: dict[str, Any] | None,
    latest_decision: Decision | None,
    prepare_spend: float,
    persona_rewrite_spend: float,
    poll_spend: float,
    input_tokens: int,
    output_tokens: int,
) -> ClaimSummary:
    if latest_decision is not None:
        status = latest_decision.status
        values = _stats_values(latest_decision.stats)
    elif state is not None:
        status = state["status"]
        values = _stats_values(state)
    else:
        status = "polling"
        values = _stats_values({})
    social = values["social_sentiment"]
    mean = values["sentiment_mean"]
    gap = None if social is None or mean is None else mean - social
    return ClaimSummary(
        aggregated_claim_id=str(claim["id"]),
        canonical_text=claim["canonical_text"],
        status=status,
        n=values["n"],
        p_believe=values["p_believe"],
        ci_low=values["ci_low"],
        ci_high=values["ci_high"],
        sentiment_mean=mean,
        sentiment_ci_low=values["sentiment_ci_low"],
        sentiment_ci_high=values["sentiment_ci_high"],
        social_sentiment=social,
        gap=gap,
        prepare_spend_usd=prepare_spend,
        persona_rewrite_spend_usd=persona_rewrite_spend,
        poll_spend_usd=poll_spend,
        total_spend_usd=prepare_spend + persona_rewrite_spend + poll_spend,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def run_simulation(
    store: Store,
    sim_run: str,
    claim_ids: list[str] | str | int | None,
    settings: Settings,
    poll_batch_fn: Callable[[list[dict[str, Any]]], list[Any]],
    prepare_fn: Callable[[], Usage],
    log: Callable[[str], None] | None = None,
    sample_fn: Callable[..., list[Respondent]] | None = None,
    rewrite_fn: Callable[..., Any] | None = None,
    llm: LLM | None = None,
) -> list[ClaimSummary]:
    prepare_usage = prepare_fn()
    if not isinstance(prepare_usage, Usage):
        raise TypeError("prepare_fn must return Usage")
    prepare_spend = prepare_usage.cost_usd(settings)
    selected_ids: list[str] | None
    limit: int | None = None
    if isinstance(claim_ids, str):
        selected_ids, limit = parse_claim_selection(claim_ids)
    elif isinstance(claim_ids, int):
        if claim_ids < 1:
            raise ValueError("claim count must be positive")
        selected_ids, limit = None, claim_ids
    else:
        selected_ids = claim_ids
    claims = store.get_testable_aggregated_claims(selected_ids, limit)
    active: dict[str, dict[str, Any]] = {}
    latest_decisions: dict[str, Decision] = {}
    states: dict[str, dict[str, Any] | None] = {}
    for claim in claims:
        claim_id = str(claim["id"])
        state = store.get_sampling_state(claim_id, sim_run)
        states[claim_id] = state
        if state is None or state["status"] not in TERMINAL_STATUSES:
            active[claim_id] = claim

    strata = [Stratum(id=str(row["id"]), share=float(row["share"])) for row in store.list_strata()]
    activity_meter = UsageMeter()
    poll_spend = 0.0
    poll_input_tokens = 0
    poll_output_tokens = 0
    known_costs: list[float] = []
    lazy_llm = llm
    metered_llm: MeteredLLM | None = None

    def get_llm() -> LLM:
        nonlocal lazy_llm, metered_llm
        if lazy_llm is None:
            lazy_llm = make_llm(settings)
        if metered_llm is None:
            metered_llm = MeteredLLM(lazy_llm, activity_meter)
        return metered_llm

    def queue_rewrite(claim_id: str) -> None:
        if rewrite_fn is not None:
            rewrite_fn(store, get_llm(), claim_id)
            return
        from .agents.question_designer import queue_rewrite as queue_question_rewrite

        queue_question_rewrite(store, get_llm(), claim_id)

    sampler = sample_fn or select_respondents
    while active:
        tasks: list[dict[str, Any]] = []
        before_counts: dict[str, int] = {}
        terminal_ids: list[str] = []
        for claim_id, claim in list(active.items()):
            question_set = store.latest_question_set(claim_id)
            if question_set is None:
                raise ValueError(f"no question set prepared for aggregated claim {claim_id}")
            question_set_id = str(question_set["id"])
            responses = store.responses_for(claim_id, sim_run, question_set_id)
            before_counts[claim_id] = len(responses)
            social_sentiment = store.get_social_sentiment(claim_id)
            decision = decide(responses, strata, social_sentiment, settings.stopping_config())
            if decision.status == "capped":
                queue_rewrite(claim_id)
            store.upsert_sampling_state(claim_id, sim_run, decision.stats, decision.status)
            latest_decisions[claim_id] = decision
            states[claim_id] = None
            if decision.status in TERMINAL_STATUSES:
                terminal_ids.append(claim_id)
                if decision.status == "capped":
                    _write_log(log, f"claim {claim_id} capped; queued a question rewrite for the next run")
                continue
            batch_no = store.max_batch_no(claim_id, sim_run) + 1
            respondents = sampler(
                store,
                get_llm(),
                sim_run,
                claim_id,
                decision.next_batch_allocation,
                settings,
            )
            for respondent in respondents:
                tasks.append(
                    {
                        "sim_run": sim_run,
                        "batch_no": batch_no,
                        "aggregated_claim_id": claim_id,
                        "question_set_id": question_set_id,
                        "respondent_id": respondent.id,
                    }
                )
        for claim_id in terminal_ids:
            active.pop(claim_id, None)
        if not active:
            break
        if not tasks:
            _write_log(log, "No respondents were available; leaving active claims in polling state.")
            break
        average_cost = sum(known_costs) / len(known_costs) if known_costs else 0.01
        estimate = len(tasks) * average_cost
        spend_before_dispatch = prepare_spend + activity_meter.cost_usd(settings) + poll_spend
        if spend_before_dispatch + estimate > settings.max_usd:
            _write_log(
                log,
                f"SPEND CAP: refusing to dispatch {len(tasks)} polls; current spend "
                f"${spend_before_dispatch:.4f} plus estimated ${estimate:.4f} "
                f"would exceed the ${settings.max_usd:.2f} budget.",
            )
            break

        results = poll_batch_fn(tasks)
        if len(results) != len(tasks):
            raise ValueError(f"poll_batch_fn returned {len(results)} results for {len(tasks)} tasks")
        successful = 0
        for task, result in zip(tasks, results, strict=True):
            claim_id = task["aggregated_claim_id"]
            if isinstance(result, BaseException):
                _write_log(log, f"poll task failed for claim {claim_id}: {type(result).__name__}")
                continue
            if not isinstance(result, dict):
                error = "invalid task result"
                error_type = type(error).__name__ if isinstance(error, BaseException) else "reported error"
                _write_log(log, f"poll task failed for claim {claim_id}: {error_type}")
                continue
            task_cost = float(result.get("usage_usd", 0.0))
            poll_spend += task_cost
            poll_input_tokens += int(result.get("input_tokens", 0))
            poll_output_tokens += int(result.get("output_tokens", 0))
            if not result.get("ok"):
                error = result.get("error")
                error_type = type(error).__name__ if isinstance(error, BaseException) else "reported error"
                _write_log(log, f"poll task failed for claim {claim_id}: {error_type}")
                continue
            successful += 1
            known_costs.append(task_cost)
        if successful == 0:
            _write_log(log, "No poll tasks succeeded; failed tasks were not counted as attempts.")
            break
        progressed = any(
            len(store.responses_for(claim_id, sim_run, str(store.latest_question_set(claim_id)["id"])))
            > before_counts.get(claim_id, 0)
            for claim_id in active
        )
        if not progressed:
            _write_log(log, "Poll tasks reported success but no responses were persisted; stopping this run.")
            break

    persona_rewrite_spend = activity_meter.cost_usd(settings)
    total_input_tokens = prepare_usage.input_tokens + activity_meter.usage.input_tokens + poll_input_tokens
    total_output_tokens = prepare_usage.output_tokens + activity_meter.usage.output_tokens + poll_output_tokens
    total_spend = prepare_spend + persona_rewrite_spend + poll_spend
    summaries = []
    for claim in claims:
        claim_id = str(claim["id"])
        state = states.get(claim_id)
        if state is None:
            state = store.get_sampling_state(claim_id, sim_run)
        summaries.append(
            _summary(
                claim,
                state,
                latest_decisions.get(claim_id),
                prepare_spend,
                persona_rewrite_spend,
                poll_spend,
                total_input_tokens,
                total_output_tokens,
            )
        )
    _write_log(
        log,
        f"Simulation {sim_run} finished: spend=${total_spend:.4f} "
        f"(prepare=${prepare_spend:.4f}, personas+rewrites=${persona_rewrite_spend:.4f}, "
        f"polls=${poll_spend:.4f}), "
        f"input_tokens={total_input_tokens}, output_tokens={total_output_tokens}.",
    )
    return summaries
