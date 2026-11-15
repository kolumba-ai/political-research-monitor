# System C — Evidence Knowledge Graph

How it works today: [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md). Design: [`System_C_Design_Doc_for_Devin.md`](System_C_Design_Doc_for_Devin.md). Read its implementation brief first.

System C finds documents online (news, official statistics, government, legislative and court records, think tanks,
universities, fact-checkers, polls) through Exa and registered feeds, builds a timestamped evidence knowledge graph
in Postgres with a projection in past.dev, and maps System B's propositions against it.

## Status: M0 skeleton

`backend/evidencegraph/` now contains the isolated `ev_` schema and migration tracker, the Mexico source registry
seed/lookup, transactional System B proposition intake, a robots-aware publisher fetcher with document versions,
rights-aware snapshots and duplicate clustering, and the CLI. The shared Exa gateway supports metadata-only cached
searches, a PostgreSQL rate limiter, and per-system monthly budgets. M0's commands do not call Exa, Anthropic,
Oriane, or past.dev.

Run from the repository root:

```sh
cd backend
../.venv/bin/python -m evidencegraph migrate
../.venv/bin/python -m evidencegraph load-registry --country MX
../.venv/bin/python -m evidencegraph registry-lookup https://www.gob.mx/presidencia
../.venv/bin/python -m evidencegraph intake --dry-run person:MX:claudia-sheinbaum-pardo person:MX:clara-marina-brugada-molina
../.venv/bin/python -m evidencegraph intake person:MX:claudia-sheinbaum-pardo person:MX:clara-marina-brugada-molina
../.venv/bin/python -m evidencegraph queue --status queued --limit 20
../.venv/bin/python -m evidencegraph enqueue-url https://www.inegi.org.mx/
../.venv/bin/python -m evidencegraph fetch --from-queue --limit 20
```

`make ev-migrate`, `make ev-test`, and `make ev-dry-run` provide the corresponding repository shortcuts.
The database URL defaults to the System B `socialclaims` database; tests use only `socialclaims_test`. Publisher
fetching observes robots.txt, limits redirects and bytes, records blocked/retry states, and stores text/raw snapshots
only for registry rows whose licence allows full-text storage.

## Claim enrichment and past.dev (M1 slice)

`enrich` takes queued propositions (highest priority first) and, for each one:

1. Claude writes the design doc's query plan (neutral, counter-evidence, primary source, indicator, fact-checks,
   expert analysis, English); rows that don't apply are skipped. Each row is one Exa search through the shared
   gateway, asking for no text or highlights; only URL, title, date, author and score are kept (`ev_exa_request`,
   `ev_discovered_url`). Social platforms and block-listed domains are excluded.
2. Up to `ENRICH_MAX_DOCUMENTS` (8) URLs are fetched from their publishers with `Fetcher`. Page text is held in
   memory and stored only where the licence allows full text.
3. Claude reads each page and returns a summary in its own words, a stance (`supports`, `contradicts`, `context`,
   `irrelevant`), the sentence it relies on and a rationale (`ev_evidence_link`). A stance whose quote is not in
   the fetched text is kept with `quote_verified = false` and never projected.
4. The claim, each document with a verified non-irrelevant stance and its stance links are written to past.dev
   (`project.py`): `clm-…` and `lnk-…` records go to the politician's `pol-<slug>` audience, `doc-…-v<n>` records are
   project-visible. Quotes follow the storage policy (copyright ≤ 300 characters, unknown ≤ 200). Every id is kept in
   `ev_memory_record`; re-runs reuse earlier judgements and resend only changed items.

Claims reach past.dev only through this step; nothing copies Postgres to past.dev in bulk.

```sh
../.venv/bin/python -m evidencegraph intake                              # free: refresh the queue from System B
../.venv/bin/python -m evidencegraph enrich --dry-run --limit 10         # free: what would run, cost ceiling
../.venv/bin/python -m evidencegraph enrich --limit 10 --confirm-spend   # Exa + Anthropic + past.dev (billed)
```

## Deferred beyond M0

- No pgvector is installed on the current Postgres 14 instance; embeddings and vector search are deferred to M1.
- Assertion/observation extraction, the advocate-critic assessment and as-known (archive) views are not implemented;
  `enrich` judges stance per document only.
- Archive snapshots are deferred. The M0 `snapshot_as_of` and `archive_origin` columns are placeholders only.
- The shared request limiter enforces rate, but has no claim-versus-standing priority scheduler yet.
- Registry seeds are pending human review (`reviewed_at` remains null). The storage policy and publisher fetching were signed off by Mauricio on 2026-11-14 (design doc open question 3).
