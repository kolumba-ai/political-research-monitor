# Test suite proposal: Systems A, B and C

Author: Mauricio Roman · Status: proposal · 2026-11-15
Repository: `kolumba-ai/political-research-monitor`, branch `systemA-knowledge_graph` at `651c153`

## Purpose

Our outputs are claims about real politicians, so a silent error is worse than a crash: a wrong office holder, a misattributed quote or a hindsight-contaminated verdict can mislead every reader downstream. This suite therefore tests three things, in this order:

1. **The code does what it says** (lint, types, unit, integration tests).
2. **The systems agree with each other and with the outside APIs** (contract tests, end-to-end golden runs, live smoke tests).
3. **The outputs are correct about the world** (data invariants on every run, gold-set evaluations of LLM steps, human audit sampling).

Layer 3 is what most projects skip and what matters most here.

## Baseline (measured 2026-11-15)

Run on a clean checkout with `pytest --cov`, no Postgres or Neo4j available:

| Item | Result |
| --- | --- |
| Test functions | 160 (System A `tests/`: 82; System B `tests/socialclaims/`: 78) |
| Outcome | 169 passed, **1 failed**, 47 skipped (parametrized counts) |
| Skipped | All 47 are System B tests that need a Postgres test database (`SOCIALCLAIMS_TEST_DATABASE_URL`) |
| Line coverage | 72% overall. Lowest: `socialclaims/capture.py` 16%, `identity.py` 20%, `accounts.py` 24%, `polgraph/monitors.py` 28%, `directories.py` 37%, `polgraph/cli.py` 0% (all mostly the Postgres-dependent or CLI paths) |
| Python lint | None configured. Ruff with default rules plus bugbear: 2,163 findings, mostly line length; real ones include 8 unused imports and 4 `B023` (closures capturing a loop variable, a known source of wrong results) |
| Formatting | 58 of 65 Python files would be reformatted |
| Type checking | None |
| CI | None (no `.github/workflows`) |
| Frontend | `frontend-3d` has `oxlint`, `tsc -b` and a browser smoke script; `frontend/` (plain JS) has nothing |

**The failing test is a real bug.** `tests/test_pipeline.py::test_incremental_refresh_skips_fresh_stages` fails every time with `UNIQUE constraint failed: pipeline_runs.run_id`. `polgraph/pipeline.py:83` builds `run_id` from a timestamp with one-second resolution, so two runs of the same country in the same second collide. Fix: add a random or monotonic suffix (e.g. `run-MX-20261114T230338Z-<6 hex>`), and keep a test that starts two runs back to back.

**Without CI, the 47 Postgres tests have probably never run outside one developer's machine.** Making them run on every change is the first priority.

## Test layers at a glance

| # | Layer | What it catches | When it runs | Budget |
| --- | --- | --- | --- | --- |
| 0 | Static checks | Style, dead code, type errors, secrets, vulnerable dependencies, bad SQL | Every commit (pre-commit) and every PR | < 1 min |
| 1 | Unit tests | Logic errors in pure functions | Every PR | < 1 min |
| 2 | Property-based tests | Edge cases nobody thought of (accents, numbers, URLs, dates) | Every PR | < 1 min |
| 3 | Contract tests | Drift between our code and Oriane, Exa, past.dev, Anthropic, and between Systems A→B→C | Every PR | < 1 min |
| 4 | Integration tests | Code against real Postgres, SQLite/Neo4j graph stores, HTTP fakes | Every PR (Postgres, SQLite); nightly (Neo4j) | < 5 min |
| 5 | End-to-end golden runs | The whole pipeline, recorded inputs to final outputs | Every PR (small fixture); nightly (full fixture) | < 5 min / 20 min |
| 6 | Live smoke tests | Real API auth, schema and behaviour changes | Nightly, opt-in, budget-capped | < $0.10/night |
| 7 | Data invariants | Wrong or inconsistent production outputs | After every pipeline run, on the real database | seconds |
| 8 | Model evaluations | LLM extraction, attribution and stance accuracy | On every prompt/model change; weekly sample | Measured |
| 9 | Human audit | Errors that automated checks cannot see | Weekly sample | Reviewer time |
| 10 | Non-functional | Rate limits, throughput, privacy, compliance, security | Nightly and before releases | — |
| 11 | Frontend | Build, lint, rendering, accessibility | Every PR touching the UIs | < 3 min |

## Layer 0 — Static checks

| Tool | Scope | Configuration |
| --- | --- | --- |
| `ruff check` | All Python | Rules `E, F, W, I, B, UP, S, BLE, RUF, PERF, DTZ`; line length 120; `DTZ` matters because every timestamp must be timezone-aware UTC |
| `ruff format` | All Python | Applied once in a single formatting-only commit, then enforced |
| `mypy` | `polgraph`, `socialclaims`, `evidencegraph` | Start with `--strict` on `ids.py`, `evidence.py`, `eligibility.py`, `claims.py`, `query.py`; widen module by module |
| `sqlfluff lint` | `*/migrations/*.sql` | Postgres dialect; also a custom check that every `CREATE TABLE` names its timestamp columns `TIMESTAMPTZ`, never `TIMESTAMP` |
| `gitleaks` | Whole repo and history | Blocks commits with API keys; allowlist only `.env.example` |
| `pip-audit`, `npm audit` | Dependencies | Fail on high severity |
| `check-jsonschema` | `socialclaims/taxonomy/*.json`, `knowledge_base/**/*.json`, `structures/*` | Schemas committed under `schemas/` |
| `oxlint`, `tsc -b` | `frontend-3d` | Already configured; add to CI |
| `markdown-link-check` | `docs/**` | Design docs link to each other and to APIs |

Put these in `.pre-commit-config.yaml` so developers and Devin get them locally, and run the same hooks in CI.

## Layer 1 — Unit tests

Existing unit tests stay. Below are the additions, chosen where an error would produce a wrong output instead of a crash.

### System A (`polgraph`)

- `ids.py`: `canonical_url` (tracking parameters, fragments, trailing slash, case), `source_id` stability across equivalent URLs, `person_id` and `party_id` merging of name variants ("Claudia Sheinbaum" = "Claudia Sheinbaum Pardo"; Spanish particles "de", "de la", "del").
- `evidence.py`: `authority_tier` for every domain pattern (`gob.mx`, subdomains, institutional and reference lists); `classify` boundaries (exactly `MIN_INDEPENDENT_DOMAINS`, one official source, conflicting); two pages on the same registrable domain never count as independent.
- `eligibility.py`: each democracy-index classification against the accepted lists; `unknown` can never pass; the expanded gate for partially democratic countries (Mexico) once it ships, with a test that it changes `pipeline_mode` only through the documented path.
- `structure.py` and `AGENTS.md` step 6 rules as tests: seat counts equal the Structure Spec; no country-wide municipal tier for federal countries; every municipal unit has its state as an ancestor; selection methods match the spec.
- `pipeline.py`: `run_id` uniqueness for back-to-back runs (the current bug).
- `exa_gateway.py`: budget reservation never lets concurrent calls exceed the run or lifetime cap; cache key changes when any request parameter changes; retries only on 429/5xx/timeouts.

### System B (`socialclaims`)

- `query.py`: the seven branches appear only when allowed (handle branches only for `verified` accounts); bare surnames never used unless added as a `query_term`; ≤ 500 filter values; `query_id` unchanged when only the date window or `id.excludes` changes, changed when identity or terms change.
- `capture.py` credit math: the page limit shrinks to fit both monthly and per-run ceilings for each projection (2/3/4 credits per row); status handling 206 → `partial`, 402 → stop, 416 → end, 400 → `invalid_query`.
- `segments.py`: comments never become claim input; segment IDs deterministic.
- `claims.py`, the highest-risk module:
  - `align`: exact, accent-insensitive and fuzzy matches; refuses quotes not in the cited segment.
  - Spanish numbers: "6 mil 500 millones" → 6500 millones; "casi 30 años"; "por ciento"; decimals with comma and point; "mil" scaling (recent commits `checks-4` and `checks-5` fixed exactly these, so each fix needs a pinned regression test).
  - Negation: "no ha subido ni subirá" negates the predicate; "no solo… sino" does not.
  - `_prop_id`: identical for reordered values, accents and casing; different for a changed number, place, period or negation.
  - Speaker rule: the politician is never `speaker` unless Phase 2 attributed speech to them.
  - Topics must exist in the country taxonomy.
- `relevance.py`: bucket thresholds (`keep` ≥ 0.5, `review` ≥ 0.3).
- `identity.py`: a changed alias creates a new `politician_ref` version and a `reconciliation_job`; a vanished ID opens `id_missing`; redirects resolve transitively without loops.

### System C (`evidencegraph`, when built)

- Document IDs equal `polgraph.ids.source_id` for the same discovered URL (shared-ID parity test).
- Date extraction priority (page metadata → dataset metadata → URL → Exa) and `date_confidence`.
- Licence policy: copyrighted documents never get full text stored; quotes ≤ 300 characters, ≤ 2 per document; Exa text never persisted.
- Statistical comparison: rounding tolerance ("más de 600", "casi 30 años"), comparative claims over a series ("lowest in 29 years"), methodology breaks downgrade to `context`.
- Independence grouping: a claimant-attributed figure is never independent; echoes within 72 hours of an official release join its group; syndicated copies are one group.
- Time views: no evidence with event time after the cutoff ever enters `as_known`.
- Assessment rules: each label's rule as a table-driven test, including "claimant-only evidence caps at `insufficient_evidence`".

## Layer 2 — Property-based tests (Hypothesis)

Generated inputs find the cases hand-written tests miss. Each property is a few lines:

- `canonical_url(canonical_url(u)) == canonical_url(u)` (idempotent), and tracking parameters never change `source_id`.
- `_prop_id` is invariant under value reordering, accent stripping and whitespace, and changes when any number changes.
- Spanish number parsing round-trips: render a random number in words and digits, parse it back.
- `align(quote, source)` never returns offsets outside `source`, and the returned span normalizes to the quote.
- Name variants: every generated variant of a System A name maps back to the same `person_id`.
- Rate limiter: for random arrival patterns, calls per second never exceed the configured rate.
- Credit planner: for random ceilings and projections, planned spend never exceeds either ceiling.
- Timestamps: every parser returns timezone-aware UTC or `None`, never a naive datetime.

## Layer 3 — Contract tests

| Contract | Test |
| --- | --- |
| Oriane | Every request `query.py` and `capture.py` build validates against `docs/system_b/oriane-integration-connect.yaml` (OpenAPI request schemas); every fake response in the test fakes validates against the response schemas, so fakes cannot drift from the real API |
| Exa | Requests from `ExaGateway` validate against the parameter set of the pinned `exa-py` version; recorded responses (see Layer 5) match the fields we read (`url`, `title`, `publishedDate`, `costDollars`) |
| past.dev | Request bodies validate against past.dev's published `openapi.json` (pinned copy in the repo); IDs, audience slugs (2–40 lowercase letters, digits, dashes) and content size checked before sending |
| Anthropic | The `record_claims` tool schema is valid JSON Schema; stored responses in `claim_extraction.response` validate against it |
| System A → B | The bundle contract: `validate_bundle` on every committed bundle under `knowledge_base/bundles/`; hash recomputes; required paths present. Run on every System A export |
| System B → C | A schema-snapshot test: System C's intake query runs against the System B migrations (`001`–`005`) in a fresh database; renaming a System B column breaks this test, not production |
| System C → B | The view `ev_proposition_evidence_v` columns are snapshot-tested |

## Layer 4 — Integration tests

- **Postgres in CI.** A `postgres:16` service container with pgvector; `SOCIALCLAIMS_TEST_DATABASE_URL` set. This alone turns the 47 skipped tests on.
- **Migrations.** Apply all migrations to an empty database; apply them twice (they use `IF NOT EXISTS`, so the second run must be a no-op); check every table has its required timestamp columns `NOT NULL`.
- **Graph stores.** The existing `store` fixture runs SQLite always and Neo4j when `POLGRAPH_TEST_NEO4J_WIPE=1`; in CI, run Neo4j nightly in a disposable container so the wipe is safe.
- **HTTP fakes.** `httpx.MockTransport` (already used) for Oriane; the same pattern for past.dev and Anthropic. For System C's fetcher, a local HTTP server fixture serving HTML, PDF, redirects, `robots.txt` disallow, 429 and slow responses.
- **API.** FastAPI `TestClient` for `polgraph/api.py` and `world_api.py`: every endpoint, error codes, and that research-only data is labelled as such.
- **CLI.** `polgraph/cli.py` is at 0% coverage: one test per command with `--dry-run` (or fakes) proving argument parsing and wiring.
- **Resumability.** Kill a capture run after page 1 (simulated exception), rerun, and assert no duplicate `raw_data` rows, no double-counted credits, and `id.excludes` covering page 1.

## Layer 5 — End-to-end golden runs

One fixture politician flows through every system with all external calls replayed from recordings ("cassettes"), and the final outputs are compared with approved golden files.

```text
System A knowledge_base fixture (Clara Brugada, CDMX)
  → bundle → politician_ref
  → Oriane capture (recorded pages)
  → segments → speaker roles
  → claims (recorded Anthropic responses)
  → System C intake → Exa (recorded) → fetch (local fixture pages) → extraction (recorded) → evidence map → assessment
  → past.dev projection (fake that records requests)
```

- **Small fixture** (5 posts, 10 claims) on every PR; **full fixture** (the 180-post Brugada pilot, once re-run through System B) nightly.
- Golden files are normalized JSON (sorted keys, timestamps replaced by placeholders, IDs kept). A diff fails the test; a reviewer approves intended changes with `make golden-update`, and the diff is part of the PR.
- Recordings are refreshed deliberately, never automatically. Recordings contain no secrets (scrubbed headers) and no Exa page text in committed form (store Exa recordings with URLs and titles only, and fixture pages we wrote or that are open-licensed).

## Layer 6 — Live smoke tests (nightly, opt-in)

Marked `@pytest.mark.live`, skipped unless `RUN_LIVE=1`, run nightly with real keys from CI secrets:

| Service | Call | Cost | Asserts |
| --- | --- | --- | --- |
| Oriane | `contents/search` with a phrase that matches nothing | 0 credits (empty results are free) | 200, response shape |
| Exa | One search, `numResults: 1`, no contents | ≈ $0.007 | 200, fields present, `costDollars` present |
| past.dev | Ingest one data point to a `ci-smoke` audience, poll until settled, recall it, delete it | ≈ 3 credits | Round trip, deletion 204 |
| Anthropic | One `record_claims` call on a fixed post | ≈ 2k tokens | Tool output validates against the schema |

Any failure opens an alert: it usually means an API changed under us.

## Layer 7 — Data invariants on real outputs

The checks most directly tied to "all outputs are correct". They run as SQL (Postgres) and graph queries after every pipeline run, write results to an `invariant_check` table, and block export when a blocking check fails.

### System A

| Check | Blocking |
| --- | --- |
| Every `Person` with a current `HOLDS_OFFICE` has a `Term` with `evidence_status` in (`verified`, `official_single`, `single_source`); no `unsupported` stored | Yes |
| Every `Term` and `PoliticalOffice` has ≥ 1 `SUPPORTED_BY → Source` | Yes |
| Seat counts per office ≤ the Structure Spec number; gaps listed | No (report) |
| No two current holders of a single-seat office | Yes |
| Term dates: `start ≤ end`; current terms have `end` null or in the future | Yes |
| `pipeline_mode` of every node matches its country's eligibility | Yes |
| Every node's `last_verified` within its stage TTL (`TTL_OFFICEHOLDERS_DAYS` etc.) | No (staleness report) |

### System B

| Check | Blocking |
| --- | --- |
| Every `raw_data` and `distilled_claim` row has its required timestamps; `published_at ≤ captured_at`; `stated_at = post.published_at` | Yes |
| Every claim with `grounding = 'exact'` re-aligns: its `quote` is found again in its segments today | Yes |
| No claim from a `comment` segment | Yes |
| `speaker_id` = the politician only where a speaker role with evidence exists | Yes |
| Every claim's numbers appear in its quote (`proposition_values_not_in_quote` count = 0 for exported claims) | Yes |
| Credits in `run.credits_used` equal the sum in `oriane_usage` for that run, and the monthly total ≤ ceiling | Yes |
| No `post` without a `run_post`; no `run_post` pointing at a superseded `politician_ref` | Yes |
| `EXCLUDED_FIELDS` (e.g. `profileEmails`) absent from every stored raw file (scan) | Yes |
| Raw files mode 600; expired files purged by `retain_until` | Yes |
| Share of claims in `review` or `failed` grounding; sudden rises alert | No (trend) |

### System C

| Check | Blocking |
| --- | --- |
| No `as_known` evidence link with evidence time after the proposition's `stated_at` | Yes |
| Every stance link's quote is present in the stored document version | Yes |
| Evidence-map counts by independence group recompute exactly from the links | Yes |
| No claimant-attributed evidence counted as independent | Yes |
| No Exa text in any table, cache or past.dev payload (scan for stored highlights) | Yes |
| Copyrighted documents: no full text stored; quotes ≤ 300 characters, ≤ 2 per document | Yes |
| `ev_document.document_id = polgraph.ids.source_id(url)` for every row | Yes |
| Every past.dev `memory_record` has `completed_at`; a weekly sample recalls each kind and finds it | No (report) |

## Layer 8 — Model evaluations (LLM steps)

Unit tests cannot tell whether an LLM extracted the right claims. Gold sets can.

| Eval | Gold set | Metrics | Release gate (initial) |
| --- | --- | --- | --- |
| Claim extraction (System B) | 150 posts hand-labelled by two annotators, balanced by platform, speaker type, party and topic | Claim recall and precision; quote exactness; number exactness; negation accuracy | Recall ≥ 0.85, precision ≥ 0.85, numbers 100% exact |
| Speaker attribution (System B) | Same posts | Accuracy of `speaker_status` and `relation_to_politician`; false "politician said it" rate | False attribution ≤ 1% |
| Proposition clustering (System B) | 300 claim pairs incl. hard near-duplicates (same topic, different figure or date) | Pairwise precision and recall | Precision ≥ 0.98 (merging different claims is the worse error) |
| Document assertion extraction (System C) | 100 documents across source classes | Assertion recall/precision; attribution accuracy | Both ≥ 0.85 |
| Stance (System C) | 300 proposition–passage pairs | Macro-F1; contradiction recall | Macro-F1 ≥ 0.80 |
| Assessment (System C) | 200 propositions with fact-checker verdicts (Verificado, El Sabueso) | Agreement; confusion between `supported` and `contradicted` | Zero `supported` ↔ `contradicted` swaps on the gold set |

Rules:

- Inter-annotator agreement (Cohen's κ) reported per gold set; items where annotators disagree are adjudicated, not dropped.
- Every prompt, model or `CHECKS_VERSION` change runs the relevant evals; results are stored with the version (System B already versions extraction and can regrade stored responses without new LLM calls, which makes these evals cheap).
- Gold sets are split into a development half (used while tuning) and a held-out half (used only for the release gate).
- Report results per party and per politician to catch systematic bias.

## Layer 9 — Human audit

A weekly random sample, stratified by system: 20 System A office holdings, 30 System B claims, 20 System C evidence maps. Reviewers check them against the sources and record errors by type in the existing `review` table. The error rate and its trend are the headline quality metric; every error found becomes a regression test or a gold-set item.

## Layer 10 — Non-functional tests

- **Rate limits.** Under 32 concurrent callers, the Exa gateway stays at or below the configured rate (fake clock); past.dev writers stay under 500 requests per minute.
- **Throughput.** Nightly benchmark on recorded data: capture pages per minute, claims per minute, evidence maps per minute; alert on > 30% regression.
- **Privacy.** Logs never contain API keys, emails or full raw payloads (log-scanning test); data-protection deletion by `profile_id` removes rows in Postgres and the matching past.dev data points.
- **Compliance.** `robots.txt` disallow is respected by the System C fetcher (fixture server); takedown removes a document and everything derived from it.
- **Security.** Dependency audit (Layer 0); FastAPI endpoints reject unexpected methods and oversized inputs; no SQL built by string formatting with user input (ruff `S608`).
- **Resilience.** Fault injection in fakes: 429 storms, timeouts, malformed JSON, partial pages; the pipeline ends in a resumable state with no duplicate rows.

## Layer 11 — Frontend

- `frontend-3d`: `oxlint`, `tsc -b`, `vite build`, and the existing `scripts/smoke.mjs`, all in CI. Add Playwright: the explorer loads a fixture graph, search finds "Clara Brugada", and the 2D fallback renders when WebGL 2 is disabled (recent commit `651c153` added that path).
- Accessibility: `axe-core` scan of the main views.
- `frontend/` (plain JS): a Playwright smoke test that the page loads against the API fixture and shows a person's path with sources.

## Organisation and tooling

**Markers** (in `backend/pyproject.toml`):

| Marker | Meaning | Default |
| --- | --- | --- |
| (none) | Unit and property tests | Always run |
| `integration` | Needs Postgres or a local server | Run in CI; skipped locally without a database |
| `neo4j` | Needs Neo4j | Nightly |
| `golden` | End-to-end replay | Small on PR, full nightly |
| `live` | Real external APIs, costs money | Nightly with `RUN_LIVE=1` |
| `eval` | Gold-set evaluation | On prompt/model change and weekly |

**Make targets:** `lint`, `typecheck`, `test` (unit + property), `test-integration`, `test-golden`, `test-live`, `eval`, `invariants` (run Layer 7 against a database), `ci` (everything a PR runs).

**Directory layout:** keep `backend/tests/` for System A and `backend/tests/socialclaims/`; add `backend/tests/evidencegraph/`, `backend/tests/contracts/`, `backend/tests/e2e/` (with `cassettes/` and `golden/`), `backend/evals/` (gold sets and runners), and `backend/invariants/` (SQL and graph checks, one file per system).

## CI pipeline (GitHub Actions)

| Workflow | Trigger | Jobs | Target time |
| --- | --- | --- | --- |
| `pr.yml` | Every pull request and push | Static checks; unit + property; contract; integration with Postgres service; small golden run; frontend lint/build | < 10 min |
| `nightly.yml` | 03:00 UTC | Neo4j integration; full golden run; live smoke; throughput benchmark; dependency audit | < 30 min |
| `eval.yml` | Changes under `claims.py`, prompts, taxonomy, model settings, or manual | Relevant gold-set evals; posts a metrics table to the PR | Varies |
| `invariants` | After every production pipeline run (called by the run itself) | Layer 7 checks; blocks export on failure | < 2 min |

**Merge rules:** all `pr.yml` jobs green; coverage may not drop; changed files ≥ 85% line coverage; eval gates met when `eval.yml` ran.

**Coverage targets:** 85% line coverage overall within two months (from 72%), 95% for `ids.py`, `evidence.py`, `eligibility.py`, `claims.py`, `query.py`, `capture.py` credit logic, and all System C mapping and assessment code. Branch coverage reported from day one.

## Rollout

| Phase | Work | Outcome |
| --- | --- | --- |
| 1 (week 1) | Fix the `run_id` bug; add `pr.yml` with Postgres service, ruff (lint only, formatting later), existing tests; `.pre-commit-config.yaml`; `gitleaks` | The 47 skipped tests run on every change; no secrets can be committed |
| 2 (weeks 2–3) | One formatting commit; mypy on core modules; contract tests (Oriane spec, bundle, B→C schema snapshot); Layer 7 invariants for Systems A and B; CLI tests | Drift and bad outputs caught automatically |
| 3 (weeks 3–5) | Golden end-to-end runs with cassettes; property tests; nightly workflow with Neo4j and live smoke | Whole-pipeline regressions caught; API changes detected within a day |
| 4 (weeks 4–8) | Gold sets and `eval.yml` for claim extraction and attribution; human audit routine; frontend Playwright | Measured LLM accuracy with release gates |
| 5 (with System C) | System C unit, invariant and eval suites as specified in its design doc | System C ships with its tests |

## Open questions

1. Who are the qualified annotators for the gold sets, and how much of their time is available? The extraction gold set (150 posts, two annotators) is roughly 3–4 days of work.
2. CI runners: GitHub-hosted is enough for the PR suite; do we have a budget for nightly live tests (≈ $3/month) and evals (LLM tokens)?
3. Where do live-test secrets live (GitHub Actions secrets, or another store)?
4. Should invariant failures page someone, or only block export and post a report?
5. Can recorded Oriane pages (which contain creators' handles) be committed to the repo for golden tests, or must they be synthetic? Recommendation: synthetic or scrubbed fixtures in the repo; real recordings in private storage fetched by CI.
