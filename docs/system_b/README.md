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

Thumbnails: capture stores Oriane's `thumbnailMediaUrl`/`thumbnailMediaId` on `post`. For posts captured before
that, `thumbnails` backfills them from the saved raw payloads, then downloads the images to `THUMBNAIL_DIR`
(default `var/socialclaims/thumbnails/<platform>/<platform_id>.webp`) and records `post.thumbnail_path`. It makes no
Oriane API call: it only fetches HTTPS URLs on `*.oriane.xyz` (Oriane's public static CDN), without following
redirects. A post is skipped when its recorded file exists under the output directory; a recapture that reports a
new thumbnail URL clears `thumbnail_path`, so the next run fetches the new image:

```bash
cd backend && ../.venv/bin/python -m socialclaims thumbnails --format video   # --no-download: URLs only
```

Coverage caveat for every output: only Oriane-indexed content, roughly the last 3 months, up to a week of
indexing lag, creators with 5,000+ followers. "No match" is not evidence that nothing was said.
The pull also writes `evidence.jsonl` and `documents.jsonl` (the System C evidence links and the documents they
cite, joined to claims by `proposition_id`; skip with `--no-evidence`) and, when `DATABASE_URL` is set, fills the
post link, account and format that past.dev leaves out from the Postgres `post` table, read-only (skip with
`--no-post-links`). `--workers N` reads past.dev texts in parallel.

## Storylines (claims by week)

`storylines` groups each politician's statements above the strict proposition level, into `storylines.json` next
to the export, with every decision appended to `storyline_decisions.jsonl`:

* **Version:** the same claim reworded. Different figures, negation, place or period can never be one version;
  this is enforced in code whatever the judge answers.
* **Storyline:** one specific claim with its versions, updates and counter-claims. A shared topic is not enough.

Each new statement is shortlisted against the politician's existing versions and judged by a model
(`STORYLINE_MODEL`, default `CLAIM_MODEL`): `same_version`, `same_storyline_different_version`, `counter_claim` or
`unrelated`, with a confidence and a reason. Answers under `STORYLINE_REVIEW_THRESHOLD` (0.75) are not applied;
they wait in `storylines-review`, which can `--accept`, `--reject`, `--move` and `--rename`. The run is billed and
needs `--confirm-spend` (`--dry-run` only counts); `--workers N` judges politicians in parallel.

`GET /api/claims/{ISO2}/storylines/{person_id}` returns the week grid and, with `?storyline=<id>[&week=YYYY-MM-DD]`,
one storyline in full: versions, every post with its link and account, each statement and its evidence documents.
The explorer shows it in the politician page's "Storylines by week" tab.

## Social metrics per politician

Two files next to the claims export feed the explorer's Metrics page and the Metrics tab on each politician:

| Command | Writes | Source |
| --- | --- | --- |
| `metrics-export` (`make sc-metrics`) | `metrics_posts.jsonl`: every captured post per politician with its latest views, likes, comments and shares | System B Postgres, read-only, no API call |
| `metrics-import <checkout>` | `metrics_perception.jsonl`: per politician and video, how the video presents them and the stance counts of its top comments | A [LatamPoliticalMonitor](https://github.com/kolumba-ai/LatamPoliticalMonitor) checkout |

* **Presentation index**: +1 positive, 0 neutral, -1 negative per politician–video pair, averaged, shown from -100 to +100.
  A positive video from the politician's own account reads as official communication, from another account as
  favourable third-party content; a negative one as critical content.
* **Feedback index**: (for - against) / human comments, -100 to +100. Likely bots are counted apart and a single
  video needs 3 human comments for its own label.

`label-perception` produces the same labels for System B's own posts: one model call per politician and post reads
the caption, transcript, on-screen text and stored top comments, and writes `post_presentation` and `comment_stance`
(migration 007). It is billed; `--dry-run` is read-only and prints pending posts, tokens and an estimated cost at
`PERCEPTION_USD_PER_MTOK_IN` / `_OUT`. System B keeps no commenter handles, so a comment is a likely bot only when the
same text (12 characters or more) sits under 3 or more posts. `metrics-export` writes these labels to
`metrics_labels.jsonl`; where an imported label exists for the same politician and video, the imported one is shown.

The import maps the monitor's politicians to System A ids (a reviewed crosswalk in `socialclaims/metrics.py`, then an
exact full-name match); the rest are listed without a profile link. Commenters' handles and texts are never copied,
only counts. `GET /api/metrics/{ISO2}/summary` and `/api/metrics/{ISO2}/politicians/{person_id}` serve the result, and
the claim list carries each post's engagement and labels.
