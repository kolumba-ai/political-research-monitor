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

Copy `.env.example` to `.env` and set `DATABASE_URL`, `ANTHROPIC_API_KEY`, and `OPENAI_API_KEY`. The remaining values are optional tunables; defaults live in `system_d/config.py`. Export the values into the shell before running the local modules:

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

## Tests

Database integration tests use only a dedicated local PostgreSQL 14 database with pgvector. Create `system_d_test` locally and set `SYSTEM_D_TEST_DATABASE_URL` to its localhost connection URL; never point this variable at Neon or another hosted database.

```bash
export SYSTEM_D_TEST_DATABASE_URL="<local PostgreSQL 14 URL for system_d_test>"
.venv/bin/python -m pytest --cov=system_d --cov-report=term-missing
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Unit tests use fake LLMs and HTTP transports; the test suite does not make live model calls or run Modal functions.
