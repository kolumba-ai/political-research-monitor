# System D

System D compares social-media sentiment about political claims with responses from a stratified sample of simulated respondents. Its design, schema rationale, agent contracts, stopping rule, and build scope are documented in [`../docs/DESIGN.md`](../docs/DESIGN.md).

## Install

Use Python 3.10 or later. From this directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The Modal image uses Python 3.11 independently of the local development interpreter.

## Configure

Copy `.env.example` to `.env` and set `DATABASE_URL` and `OPENAI_API_KEY`. `LLM_MODEL` defaults to `gpt-6.1-sol` for structured calls and `SIMULATOR_MODEL` also defaults to `gpt-6.1-sol` for respondent chat (`gpt-6-luna` is cheaper but gave near-uniform answers in testing); both can be overridden. `ANTHROPIC_API_KEY` is optional and is required only when either configured model uses Anthropic. `OPENAI_BASE_URL` defaults to `https://api.openai.com`. `LLM_TIMEOUT_S` defaults to 180 seconds. `SIMULATOR_REASONING_EFFORT` defaults to `low`; set it to an empty string to omit the OpenAI chat field. The remaining values are optional tunables; defaults live in `system_d/config.py`. Export the values into the shell before running the local modules:

```bash
cp .env.example .env
set -a
source .env
set +a
```

Do not commit `.env`. Hosted Modal execution uses the existing `neon-db` and `llm-api` Modal secrets; use a pooled Neon URL only for hosted execution.

## Migrate and seed

The migration uses an advisory lock and a schema checksum, so it is safe to run repeatedly:

```bash
.venv/bin/python -m system_d.migrate
.venv/bin/python -m system_d.seed
```

The seed contains illustrative, unverified Spanish claims and comments. Social reaction sentiment and stance start unset and are scored by the social-scorer agent.

## Run a simulation

Authenticate Modal and ensure the `neon-db` and `llm-api` secrets are configured, then run from this directory:

```bash
modal run modal_app.py::run_simulation --sim-run sim-run-1 --claims all
```

`--claims` accepts `all`, a positive count for the first N claims, or comma-separated aggregate claim IDs. The simulator is the only implemented channel; WhatsApp and Vapi are phase-two stubs.

## Stopping criteria

Each aggregated claim is polled in batches within a `sim_run`. After every batch the controller calls `decide()` in `system_d/stopping.py` (DESIGN.md §4). `decide()` applies these checks in order, and the first one that matches sets the claim's status in `claim_sampling_state`:

| # | Check | Result |
|---|---|---|
| 1 | `n_completed ≥ MAX_CALLS` (400), or attempts including refusals and incomplete calls `≥ 2 × MAX_CALLS` (800) | `capped`: stop, flag the claim inconclusive, and queue a rewritten `question_sets` version for the next run |
| 2 | No responses yet | `polling`: seed batch of `SEED_BATCH` (30), split across strata in proportion to their share |
| 3 | Any stratum with share > 0 has fewer than `MIN_PER_STRATUM` (5) completed responses | `polling`: the claim can't stop yet |
| 4 | The claim has social sentiment, and it falls outside the polled sentiment's 95% CI | `decisive`: stop |
| 5 | The half-width of the 95% Wilson CI on `p_believe` is `≤ TARGET_HALF_WIDTH` (0.07, i.e. ±7 points) | `converged`: stop |
| 6 | Otherwise | `polling`: next batch of `NEXT_BATCH` (20), one slot at a time to the stratum furthest below its quota `max(MIN_PER_STRATUM, share × n)` |

`converged`, `decisive` and `capped` are terminal: a resumed run skips claims that already have one of these statuses. `decisive` is checked before `converged`, so when both hold the claim is recorded as `decisive`.

Definitions:
- **Completed response:** the respondent answered both the belief question and the sentiment question, and did not refuse.
- **`p_believe`:** the post-stratified share answering "sí". "No sé" counts as not believing. Each response is weighted by stratum share ÷ the stratum's share of the sample. The Wilson interval uses the Kish effective sample size of those weights.
- **Polled sentiment:** the 5-point answer mapped to −1, −0.5, 0, 0.5, 1. It is post-stratified the same way, with a 95% t-interval.
- **Social sentiment:** the mean scored sentiment (−1..1) of the comments on the claim's source posts. Without it, `decisive` can't fire.

A run can also stop early. Claims that are still running then stay `polling` and can be resumed with the same `--sim-run`. This happens when:
- the next round's estimated cost would take total LLM spend over `MAX_USD` ($5); total spend covers preparation, personas and rewrites, and polls;
- no respondents are available;
- every poll task in a round fails;
- a round saves no new responses.

`SEED_BATCH`, `NEXT_BATCH`, `TARGET_HALF_WIDTH`, `MAX_CALLS`, `MIN_PER_STRATUM` and `MAX_USD` can be overridden through environment variables; their defaults are in `system_d/config.py`.

## Tests

Database integration tests use only a dedicated local PostgreSQL 14 database with pgvector. Create `system_d_test` locally and set `SYSTEM_D_TEST_DATABASE_URL` to its localhost connection URL; never point this variable at Neon or another hosted database.

```bash
export SYSTEM_D_TEST_DATABASE_URL="<local PostgreSQL 14 URL for system_d_test>"
.venv/bin/python -m pytest --cov=system_d --cov-report=term-missing
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Unit tests use fake LLMs and HTTP transports; the test suite does not make live model calls or run Modal functions.
