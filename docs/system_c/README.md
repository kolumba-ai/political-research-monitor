# System C — Evidence Knowledge Graph

Design: [`System_C_Design_Doc_for_Devin.md`](System_C_Design_Doc_for_Devin.md). Read its implementation brief first.

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

## Deferred beyond M0

- No pgvector is installed on the current Postgres 14 instance; embeddings and vector search are deferred to M1.
- Claim/assertion extraction, mapping, stance assessment, and past.dev projection are not implemented.
- Archive snapshots are deferred. The M0 `snapshot_as_of` and `archive_origin` columns are placeholders only.
- The shared request limiter enforces rate, but has no claim-versus-standing priority scheduler yet.
- Registry seeds are pending human review (`reviewed_at` remains null). The storage policy and publisher fetching were signed off by Mauricio on 2026-11-14 (design doc open question 3).
