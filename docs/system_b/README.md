# System B — Phase 0 + Phase 1 (identity contract and Oriane capture)

Code: `backend/socialclaims/`. Design: [`System_B_Design_Doc_for_Devin.md`](System_B_Design_Doc_for_Devin.md);
Oriane API: [`oriane-integration-connect.yaml`](oriane-integration-connect.yaml).

Scope: Mexico, Spanish, internal only, evidence only. No Exa validation or past.dev writes yet.
Oriane is the only social source; System B never creates politician identities.

## Flow

1. **Input bundle** (System A → B contract, `polgraph/bundle.py`): System B's only input per politician is a
   bundle holding the **country**, the **politician**, and the subgraph between them:
   `Country -HAS_GOVERNMENT_LEVEL-> GovernmentLevel [-CONTAINS-> ...] -HAS_BODY-> GovernmentBody -HAS_OFFICE->
   PoliticalOffice -HAS_TERM-> Term -TERM_HELD_BY-> Person`, plus the leaves `Person -MEMBER_OF_PARTY->
   PoliticalParty` and `Office/Term -SUPPORTED_BY-> Source`. Its `sha256` covers only that content.
   - Sources (`SYSTEM_A_SOURCE`): `bundles` reads files System A exported (`make export-bundles`, i.e.
     `python -m polgraph export-bundles --country MX PK`, → `knowledge_base/bundles/<ISO2>/`, committed with
     each export); `knowledge_base`
     (default) and `graph` build the same bundle on the fly from the export or the live store.
   - Validation (`systema.validate_bundle`) recomputes the paths from the bundle itself: wrong country, a hash
     mismatch, or a current office with no Country → office path makes the person `invalid_input` (never
     captured). A missing party leaf is only a warning.
   - Each bundle is stored once in `system_a_bundle`; `politician_ref.bundle_sha256` and each run's
     `manifest.system_a_bundle_sha256` point to the exact slice used.
   From the bundle, System B collects current/past `HOLDS_OFFICE` + `Term`, office title/keywords,
   jurisdiction (`GovernmentBody` → `GovernmentLevel`), `MEMBER_OF_PARTY` + coalition, and the country's
   `pipeline_mode`.
   - `trackable`: a current holding with `evidence_status` `verified` or `official_single`.
   - `needs_review`: only `single_source` (sent to `review`; capture needs `--allow-review`).
   - `ineligible`: no current holding. Capture is refused.
   - System A has no `Country.pipeline_mode`; a country is `production` only if `eligible` and
     `research_status == enriched`, and any `research_only` holding keeps the person `research_only`.
2. **`politician_ref` versions** (`identity.py`): one row per change of identity (name, aliases, offices, terms,
   parties, pipeline_mode) or of the stored query. Older versions get `superseded_at`; runs record the
   `politician_version` they used, so history is never relabelled. Identity changes open a
   `reconciliation_job` with a field diff; a vanished ID opens `id_missing`. Merges are explicit
   `person_id_redirect` rows (`socialclaims redirect`).
3. **Query** (`query.py`): one composite query with the seven named branches (`own`, `coauthored`, `tagged`,
   `spoken`, `written`, `onscreen`, `hashtag`). Handle/profile branches appear only for **verified**
   `politician_account` rows; name branches use `includesExactly` on full-name variants (accent-free forms,
   given name + paternal surname). A bare surname is only used if added as an explicit `query_term`.
   `query_id` = SHA-256 of the canonical query JSON; the date window and `id.excludes` are per run, so
   `query_id` is stable until the identity or the added terms change.
4. **Capture** (`capture.py`): `sort=publishedAt:desc`, `projection=full`, `limit ≤ 100`, offset paging,
   `id.excludes` with already-captured posts (newest first, within Oriane's 500-value budget), capped at
   `MAX_POSTS_PER_POLITICIAN` (1,000). Before every page the limit is shrunk to fit both the monthly and the
   per-run credit ceilings (credits = rows × 2/3/4 for basic/default/full; spend is tracked in
   `oriane_usage` since Oriane has no balance endpoint). `206` → run `partial`; `402` → stop
   (`credits_exhausted`); `416` → end of results; `400` → `invalid_query`; 429/5xx/transport errors retry
   with backoff.
5. **Storage**: each page is written once as `{request, status, captured_at, response}` (mode 600) under
   `RAW_STORAGE_DIR/<run_id>/page-NNNNNN.json`, its hash in the run manifest. `raw_data` has one row per post
   with a JSON pointer into the page, the post's SHA-256, `request_id`, `published_at`, Oriane's
   `createdAt`/`updatedAt` and `captured_at`. `EXCLUDED_FIELDS` are dropped before storage and listed on every
   row; `RAW_RETENTION_DAYS` sets `retain_until`, and `purge-expired` deletes lapsed files and keeps hashes.
   `post` keeps `first_observed_at`, `last_captured_at`, the union of matched branches and `latest_raw_id`;
   `post_metrics` appends one observation per capture; `run_post` links post ↔ run ↔ politician with the
   branch-weighted relevance score and bucket (`keep` / `review` / `low`).

## Handles

`discover-accounts` (billed, needs `--confirm-spend`) runs Oriane profile search and stores candidates as
`proposed`. Only `approve-account --by <reviewer> --source <independent URL>` marks an account `verified`,
which adds the `own` / `coauthored` / `tagged` branches and creates a new query version.

## Claims from past.dev (read path)

`pull-pastdev` exports every claim data point in the country's past.dev project (`PAST_DEV_API_KEY`) to
`CLAIMS_EXPORT_DIR/<ISO2>/` (default `var/socialclaims/claims/`, git-ignored): `posts.jsonl` (one row per post,
with caption, engagement and its claims), `claims.jsonl` (one row per claim) and `manifest.json` (counts, audiences,
per-politician totals). It needs no Postgres or Oriane call. past.dev has no list route, so it walks
`GET /trace/roots` (one source node per data point) and reads each text from `GET /trace/nodes/{id}`; texts over
4,000 characters are cut there, so those are re-read with one recall (1 credit) pinned to the post's exact time.
Payload person ids are checked against System A and, if missing, resolved by name (e.g. the stored
`person:MX:clara-brugada-molina` becomes `person:MX:clara-marina-brugada-molina`).

The API serves the export at `GET /api/claims/{ISO2}/summary` and
`GET /api/claims/{ISO2}/politicians/{person_id}?type=&speaker=&platform=&q=&limit=&offset=`; the 3D explorer
shows it in the country panel and the person inspector. Re-run `make sc-pull` to refresh; the API reloads on change.

## Commands

```bash
make sc-pull                       # export past.dev claims for MX (read-only) for /api/claims and the explorer
make sc-migrate                    # create the Postgres schema (DATABASE_URL)
make sc-list                       # MX persons from knowledge_base with tracking status
make sc-sync                       # write politician_ref versions
make sc-dry-run                    # build the pilot requests without calling Oriane
cd backend && ../.venv/bin/python -m socialclaims query "Clara Brugada"
cd backend && ../.venv/bin/python -m socialclaims capture "Clara Brugada" --max-posts 100 --confirm-spend
# only the politician's own-account posts (useful when mentions crowd them out of the newest-first window)
cd backend && ../.venv/bin/python -m socialclaims capture "Claudia Sheinbaum" --branches own --max-posts 200 --confirm-spend
cd backend && ../.venv/bin/python -m socialclaims extract-claims "Claudia Sheinbaum" --shard 0/2 --dry-run
cd backend && ../.venv/bin/python -m socialclaims regrade-claims "Claudia Sheinbaum" --from-checks checks-2
make sc-test
```

`extract-claims --shard I/N` partitions posts for parallel workers; `extract-claims --max-tokens N` sets the
Anthropic output-token limit (default 8000); `regrade-claims --from-checks` rechecks stored responses without
another LLM call.

After a name-variant fix or an `add-term exclude_variant`, re-check stored name-only matches (no API call; dry run without `--apply`). Posts whose saved text holds none of the current variants move to relevance `low`; nothing is deleted:

```bash
cd backend && ../.venv/bin/python -m socialclaims rematch person:MX:maria-lourdes-paz-reyes --apply
```

Coverage caveat for every output: only Oriane-indexed content, roughly the last 3 months, up to a week of
indexing lag, creators with 5,000+ followers. "No match" is not evidence that nothing was said.
