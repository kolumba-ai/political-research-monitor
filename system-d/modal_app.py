"""Modal application for System D's preparation and polling fleet."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

import modal

from system_d.config import Settings
from system_d.llm import Usage

SYSTEM_D_DIR = Path(__file__).resolve().parent
app = modal.App("system-d")
database_secret = modal.Secret.from_name("neon-db")
llm_secret = modal.Secret.from_name("llm-api")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_pyproject(str(SYSTEM_D_DIR / "pyproject.toml"))
    .add_local_python_source("system_d")
    .add_local_dir(str(SYSTEM_D_DIR / "db"), remote_path="/root/db")
)


def _prepare_system_d(settings: Settings) -> Usage:
    from system_d.agents.aggregator import aggregate_pending
    from system_d.agents.question_designer import ensure_question_set
    from system_d.agents.social_scorer import score_unscored
    from system_d.db import Store, get_pool
    from system_d.llm import MeteredEmbedder, MeteredLLM, OpenAIEmbedder, UsageMeter, make_llm

    usage_meter = UsageMeter()
    llm = MeteredLLM(make_llm(settings), usage_meter)
    embedder = MeteredEmbedder(OpenAIEmbedder(settings), usage_meter)
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            store = Store(conn)
            score_unscored(store, llm)
            aggregate_pending(store, llm, embedder, settings)
            for claim in store.get_testable_aggregated_claims():
                ensure_question_set(store, llm, str(claim["id"]))
    finally:
        pool.close()
    return usage_meter.usage


@app.function(
    image=image,
    secrets=[database_secret, llm_secret],
    max_containers=int(os.environ.get("FLEET_CONCURRENCY", "50")),
)
def poll_one(task: dict):
    from system_d.agents.polling import poll_task

    return poll_task(task, Settings.from_env())


@app.function(image=image, secrets=[database_secret, llm_secret])
def prepare():
    return _prepare_system_d(Settings.from_env())


@app.function(
    image=image,
    secrets=[database_secret, llm_secret],
    timeout=2 * 60 * 60,
)
def controller(sim_run: str, claims: str = "all"):
    from system_d.controller import run_simulation
    from system_d.db import Store, get_pool

    settings = Settings.from_env()
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            summaries = run_simulation(
                store=Store(conn),
                sim_run=sim_run,
                claim_ids=claims,
                settings=settings,
                poll_batch_fn=lambda tasks: list(poll_one.map(tasks, return_exceptions=True)),
                prepare_fn=lambda: _prepare_system_d(settings),
                log=print,
            )
            return [asdict(summary) for summary in summaries]
    finally:
        pool.close()


@app.function(image=image, secrets=[database_secret])
def migrate_remote():
    from system_d.migrate import main

    main()


@app.function(image=image, secrets=[database_secret])
def seed_remote():
    from system_d.seed import main

    main()


@app.local_entrypoint()
def run_simulation(sim_run: str, claims: str = "all"):
    summaries = controller.remote(sim_run, claims)
    print(
        f"{'CLAIM':60} {'STATUS':10} {'N':>5} "
        f"{'P_BELIEVE [CI_LOW, CI_HIGH]':31} {'SENTIMENT [LO, HI]':25} {'SOCIAL':>9} {'GAP':>8}"
    )
    for row in summaries:
        canonical_text = str(row["canonical_text"])
        if len(canonical_text) > 60:
            canonical_text = f"{canonical_text[:57]}..."
        belief = _format_range(row["p_believe"], row["ci_low"], row["ci_high"])
        sentiment = _format_range(row["sentiment_mean"], row["sentiment_ci_low"], row["sentiment_ci_high"])
        social = _format_number(row["social_sentiment"])
        gap = _format_number(row["gap"])
        print(
            f"{canonical_text:60.60} {row['status']:10} {row['n']:5d} {belief:>31} {sentiment:>25} {social:>9} {gap:>8}"
        )
    if summaries:
        totals = summaries[0]
    else:
        totals = {
            "total_spend_usd": 0.0,
            "prepare_spend_usd": 0.0,
            "persona_rewrite_spend_usd": 0.0,
            "poll_spend_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    print(
        f"Total spend: ${totals['total_spend_usd']:.4f} "
        f"(prepare=${totals['prepare_spend_usd']:.4f}, "
        f"personas+rewrites=${totals['persona_rewrite_spend_usd']:.4f}, "
        f"polls=${totals['poll_spend_usd']:.4f}); "
        f"tokens: {totals['input_tokens']} in / {totals['output_tokens']} out"
    )


def _format_number(value):
    return "—" if value is None else f"{value:.3f}"


def _format_range(value, low, high):
    if value is None:
        return "—"
    return f"{_format_number(value)} [{_format_number(low)}, {_format_number(high)}]"


@app.local_entrypoint()
def migrate():
    migrate_remote.remote()


@app.local_entrypoint()
def seed():
    seed_remote.remote()
