# Optional Modal deployment

Systems A (`polgraph`), B (`socialclaims`), and C (`evidencegraph`) can run on Modal without changing their local defaults. Local runs remain unchanged unless the shared limiter URLs are configured.

## Prerequisites

1. Create a Modal account and authenticate with `modal token new` (or configure the Modal token in your environment).
2. Use a hosted PostgreSQL database reachable from Modal. Modal containers cannot reach a PostgreSQL server bound to your local machine. The hosted database is used by Systems B and C and by the shared rate-limiters; install the `vector` extension for the System B M1 migrations.
3. Create the persistent run-data volume and secret:

   ```sh
   modal volume create prm-var
   modal secret create prm-secrets \
     EXA_API_KEY=... \
     ANTHROPIC_API_KEY=... \
     ORIANE_API_KEY=... \
     PAST_DEV_API_KEY=... \
     DATABASE_URL=... \
     EXA_LIMITER_DATABASE_URL=... \
     RATE_LIMITER_DATABASE_URL=...
   ```

   `DATABASE_URL`, `EXA_LIMITER_DATABASE_URL`, and `RATE_LIMITER_DATABASE_URL` should point at the hosted PostgreSQL service. The Modal secret is injected at runtime and is never baked into the image.

4. Install the Modal client into the project virtual environment:

   ```sh
   .venv/bin/pip install -r requirements-modal.txt
   ```

The app image uses Python 3.11 (the current Modal image builder requires 3.11 or later) and installs `requirements.txt`. The local Modal client remains pinned below 1.7 for the project’s Python 3.10 virtual environment. The image includes `backend/`, `knowledge_base/`, and `structures/` under `/root/app`; all three Python packages therefore resolve `REPO_ROOT` to `/root/app`. Persistent SQLite data, Exa cache, raw Oriane snapshots, EvidenceGraph snapshots, and exports live under the `prm-var` volume mounted at `/root/app/var`.

## Commands

```sh
make modal-deploy
make modal-systema ARGS="build --country MX"
make modal-systemb ARGS="capture person:MX:claudia-sheinbaum-pardo --confirm-spend"
make modal-systemc ARGS="queue --status queued --limit 20"
make modal-extract SHARDS=8 ARGS="person:MX:claudia-sheinbaum-pardo --confirm-spend"
```

The extraction CLI shard syntax is zero-based `I/N` (`0 <= I < N`); the entrypoint fans out over `0/N` through `N-1/N`. A requested shard count is clamped to the configured Anthropic container cap. The fetch entrypoint can fan out queue workers with:

```sh
.venv/bin/modal run backend/modal_app.py::fetch --workers 4 --extra-args "--from-queue --limit 20"
```

Workers claim queue rows using PostgreSQL `FOR UPDATE SKIP LOCKED`, so concurrent fetch containers do not fetch the same queued URL. Per-host spacing is still enforced by the shared PostgreSQL rate limiter. The optional single-container API can be deployed with the app; it reads System A's SQLite graph from the shared volume. The API serves the graph snapshot available when its container starts; stop and redeploy the app to force it to refresh after graph updates. Idle API containers scale down after 60 seconds.

## Concurrency defaults

| Worker | Default cap | Shared request limit / reason |
| --- | ---: | --- |
| System A | 1 | SQLite on the shared volume has one writer; Exa is limited to 8 requests/s by `EXA_LIMITER_DATABASE_URL`. |
| System B capture/other commands | 1 | Oriane 1 request/s; past.dev 2 requests/s via `RATE_LIMITER_DATABASE_URL`. |
| System B claim extraction | 10 | Anthropic 2 requests/s via `RATE_LIMITER_DATABASE_URL`; shard work is spread across containers. |
| System C general commands | 1 | Exa 8 requests/s; the existing EvidenceGraph limiter uses the configured PostgreSQL database. |
| System C fetch workers | 4 | Per-host delay is enforced across workers by the existing PostgreSQL limiter. |

Caps can be changed at deploy/run time with `MODAL_SYSTEMB_MAX_CONTAINERS`, `MODAL_CLAUDE_MAX_CONTAINERS`, `MODAL_SYSTEMC_MAX_CONTAINERS`, and `MODAL_FETCH_MAX_CONTAINERS`. CLI workers default to a six-hour timeout (`MODAL_TIMEOUT_S=21600`); Modal allows at most 86400 seconds. Shared limiter settings are `ANTHROPIC_REQUESTS_PER_SECOND=2`, `ORIANE_REQUESTS_PER_SECOND=1`, `PASTDEV_REQUESTS_PER_SECOND=2`, and `OPENAI_REQUESTS_PER_SECOND=5`; setting `CLAIM_MODEL=gpt-6.1-sol` selects OpenAI for claim extraction. The API-specific `Retry-After` header is honored on retryable 429/503 responses.
