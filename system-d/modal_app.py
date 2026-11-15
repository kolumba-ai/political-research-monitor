"""Modal application for System D's preparation and polling fleet."""

from __future__ import annotations

import os
from pathlib import Path

import modal

from system_d.config import Settings

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


def _prepare_system_d(settings: Settings) -> None:
    from system_d.agents.aggregator import aggregate_pending
    from system_d.agents.question_designer import ensure_question_set
    from system_d.agents.social_scorer import score_unscored
    from system_d.db import Store, get_pool
    from system_d.llm import AnthropicLLM, OpenAIEmbedder

    llm = AnthropicLLM(settings)
    embedder = OpenAIEmbedder(settings)
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
    _prepare_system_d(Settings.from_env())


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
            return run_simulation(
                store=Store(conn),
                sim_run=sim_run,
                claim_ids=claims,
                settings=settings,
                poll_batch_fn=lambda tasks: list(poll_one.map(tasks, return_exceptions=True)),
                prepare_fn=lambda: _prepare_system_d(settings),
                log=print,
            )
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
    print(f"{'CLAIM':36} {'STATUS':10} {'N':>5} {'BELIEVE':>9} {'SENTIMENT':>11} {'SOCIAL':>9} {'GAP':>8}")
    for row in summaries:
        believe = "—" if row.p_believe is None else f"{row.p_believe:.3f}"
        sentiment = "—" if row.sentiment_mean is None else f"{row.sentiment_mean:.3f}"
        social = "—" if row.social_sentiment is None else f"{row.social_sentiment:.3f}"
        gap = "—" if row.gap is None else f"{row.gap:.3f}"
        print(
            f"{row.aggregated_claim_id[:36]:36} {row.status:10} {row.n:5d} "
            f"{believe:>9} {sentiment:>11} {social:>9} {gap:>8}"
        )
    if summaries:
        print(
            f"Total spend: ${summaries[0].total_spend_usd:.4f}; "
            f"tokens: {summaries[0].input_tokens} in / {summaries[0].output_tokens} out"
        )


@app.local_entrypoint()
def migrate():
    migrate_remote.remote()


@app.local_entrypoint()
def seed():
    seed_remote.remote()
