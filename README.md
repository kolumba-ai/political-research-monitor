# Global Political Knowledge Graph (Exa-powered)

An evidence-grounded knowledge graph of political structures and officeholders, rooted at **Earth →
Continents → Countries → Government levels → Bodies → Offices → People**. Nothing about government
structure or officeholders is hard-coded: every fact is researched live with Exa, validated against a
strict schema, and **independently re-checked against page text fetched with Exa** before it can enter the
graph. Every persisted claim links to the sources that support it.

Researched so far: **Mexico** and **Pakistan** (government structure and verified officeholders; for Pakistan also
individual provincial-assembly members via the opt-in `legislators` stage), plus a **regime classification for all 249 countries**.

## Where Exa is called (main loop)

All Exa traffic goes through one gateway, [backend/polgraph/exa_gateway.py](backend/polgraph/exa_gateway.py),
and is driven by the research stages in [backend/polgraph/research.py](backend/polgraph/research.py):

| Exa API | Used for | Code |
| --- | --- | --- |
| `POST /agent/runs` (async, `outputSchema`) | Democracy classifications, contact-legality rules, national structure, all first-level divisions + governors, municipal structure + mayors, special jurisdictions (Mexico City boroughs) | `ExaGateway.agent_run`, `research_*` |
| `POST /contents` | Re-fetch every cited page; a claim survives only if the fetched text names the person **and** the office (and the jurisdiction) | `ExaGateway.contents`, `verify_holder`, `_verify_assessment` |
| `POST /search` | Corroboration searches for independent sources when citations are thin; publisher-restricted searches (`includeDomains`) for democracy indices | `ExaGateway.search` |
| `POST /contents` (bulk) | Whole-world regime classification: fetches the EIU Democracy Index and Freedom House status tables and classifies every country | [backend/polgraph/world.py](backend/polgraph/world.py) |
| `POST /monitors` | Daily monitors for election results, appointments, resignations and court changes; their output triggers re-verification, never direct graph writes | [backend/polgraph/monitors.py](backend/polgraph/monitors.py) |

Without Exa the pipeline produces nothing: there is no fallback data.

## Pipeline

```text
ISO 3166-1 + UN M49 registry  ──► Earth / 7 continents / 249 countries (no Exa)
classify-world  Exa Contents ► EIU + Freedom House tables ► regime class for every country
Select country
  ├─ democracy   Exa Agent ► Freedom House / V-Dem / EIU labels ► verify label next to country name on fetched page
  ├─ gate        policy: all verified, fresh sources must agree & meet accepted categories; grey zone / disagreement ⇒ review
  ├─ contact     Exa Agent ► rules by purpose × channel × sender × recipient ► fetch each cited statute
  ├─ gate        worst relevant status; `unknown` never passes; human review required
  │   gates are informational by default (nothing is hidden); --production stops here unless BOTH pass
  ├─ national    Exa Agent ► bodies, offices, selection method, leadership holders ► verify each holder
  ├─ divisions   Exa Agent ► all ISO 3166-2 divisions, executive + legislature, governors ► verify
  ├─ local       Exa Agent ► municipal structure + heads of largest municipalities ► verify
  ├─ special     Exa Agent ► jurisdictions with distinct governance (Mexico City alcaldías, Karachi towns) ► verify
  └─ legislators (opt-in) per assembly: composition ► members in 50-constituency slices + reserved seats ► verify
Verified claims ► idempotent upserts with dedup into Neo4j ► React + Cytoscape UI ► Exa Monitors ► revalidation
```

Each stage is independent and idempotent (a natural unit for a Modal job per country/stage later).

### Claim verification

For every officeholder claim the pipeline fetches the agent's cited URLs with Exa Contents and checks the
text itself (accent-insensitive; first name + a surname within two words, never across "y/and"), the
office keywords (gender-neutral stems: *presidenta/presidente*), and the jurisdiction name (so a page
about a different governor doesn't count). If fewer than `MIN_INDEPENDENT_DOMAINS` independent domains
support it, it runs an Exa Search for corroboration.

| Evidence status | Rule | Persisted? | Shown by default |
| --- | --- | --- | --- |
| `verified` | ≥ 2 independent registrable domains support it | yes | yes |
| `official_single` | one official/primary source (gov TLD, legislature, court, electoral authority) | yes | yes |
| `single_source` | one non-official source | yes, flagged | toggle |
| `unsupported` | no fetched text supports it | **no**, logged as an unresolved claim | n/a |

Democracy labels are verified only if the label appears within 400 characters of the country name on a
page from the index publisher (or Our World in Data for V-Dem/EIU). V-Dem grey-zone codes (`EA+`, `ED-`)
are detected and force human review.

## Knowledge base snapshot

The complete graph and its research provenance are committed in [knowledge_base/](knowledge_base/)
(nodes, relationships, flattened CSVs, Exa Agent outputs with citations, unresolved claims, cost ledger).
Load it with `make import-kb`, with no Exa key needed; refresh it with `make export`. See
[knowledge_base/README.md](knowledge_base/README.md).

## Regime classification (all countries)

`python -m polgraph classify-world` gives every country a `regime_group`: **Democratic**, **Partly democratic**,
**Authoritarian**, or **Unclassified** (no index covers it, mostly dependent territories). It also sets a detailed
`regime_class`, a per-index breakdown, and whether the indices agree. EIU's 2025 Democracy Index is the primary
source, because its typology (full/flawed democracy, hybrid regime, authoritarian) maps directly onto these groups;
Freedom House status is the fallback. Ratings confirmed on a publisher's own site (from the per-country
`democracy` stage) take precedence over compilation rows and are never downgraded. Country names are matched to
ISO codes exactly, never fuzzily, so Kosovo stays unmatched instead of overwriting Serbia. The classification is
informational: it does not gate or hide anything.

## Legislators (opt-in stage)

`--stages legislators [--divisions PK-PB,PK-SD]` fills in individual members of each first-level legislature:

1. One Agent run gets the assembly's composition: seat counts by type, constituency code range (e.g. PP-1…PP-297), and the official member list.
2. Members are requested in slices of 50 constituencies, plus one slice for reserved seats.
3. General seats go into a `direct_election` office. Reserved seats (women, minorities) go into their own
   `indirect_election` offices, since they're allocated by party list. Each holding records `constituency_code` and `seat_type`.
4. Each member is verified (name + assembly + constituency on a fetched page). When an official page already
   confirms a member, no extra corroboration search is run, which keeps bulk lists affordable (Balochistan, 65 seats: $1.30).

## Data model

Node labels: `Earth, Continent, Country, DemocracyAssessment, ContactRule, GovernmentLevel, GovernmentBody,
PoliticalOffice, Person, PoliticalParty, Term, Source`.

Relationships: `CONTAINS, HAS_ASSESSMENT, HAS_CONTACT_RULE, HAS_GOVERNMENT_LEVEL, HAS_BODY, HAS_OFFICE,
HOLDS_OFFICE, CONTESTS_OFFICE, MEMBER_OF_PARTY, SUPPORTED_BY, HAS_TERM, TERM_HELD_BY`.

* Canonical IDs, e.g. `country:MX`, `level:MX:state:MX-JAL`, `office:MX:state:MX-JAL:executive:governor-of-jalisco`,
  `person:MX:claudia-sheinbaum-pardo`, `party:MX:morena`, `source:<sha1(url)>`. Subdivisions use ISO 3166-2 codes.
* `PoliticalOffice.selection_method` ∈ direct_election / indirect_election / appointment / mixed / hereditary /
  ex_officio / unknown; `is_elected` is derived from **how the office is filled**, never from whether a person
  could stand for it.
* `HOLDS_OFFICE` carries `current`, `start`, `end`, `evidence_status`, `confidence`, `last_verified`,
  `pipeline_mode`; the matching `Term` node holds the `SUPPORTED_BY → Source` edges, with the supporting
  snippet on each edge. A newly verified holder of a single-seat office demotes the previous one to
  `former` (history kept).
* Dedup: people merge on the same first name plus a shared first surname (*Claudia Sheinbaum* ≡ *Claudia
  Sheinbaum Pardo*, but *Juan Pérez López* ≢ *Juan García López*); parties on acronym; offices on local title
  or title similarity within the same body.

## Setup

Requirements: Python 3.11+, Java 17+ (for Neo4j), an Exa API key. No Node.js needed (the UI loads React,
htm and Cytoscape from CDNs).

```bash
make install                      # venv + requirements
cp .env.example .env              # add EXA_API_KEY and NEO4J_PASSWORD
# Neo4j Community 5.26 (local, no Docker):
curl -L -o /tmp/neo4j.tgz https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz
mkdir -p .local && tar xzf /tmp/neo4j.tgz -C .local
.local/neo4j-community-5.26.0/bin/neo4j-admin dbms set-initial-password '<NEO4J_PASSWORD>'
make neo4j-start
```

Prefer zero setup? Set `GRAPH_BACKEND=sqlite`; the embedded store has the same upsert semantics.

## Commands

```bash
make classify          # regime classification for all 249 countries (~$0.01)
make mexico            # build/refresh Mexico
make pakistan          # build/refresh Pakistan
make mexico-production # gated mode: stops after the eligibility checks unless both gates pass
make refresh-mexico    # re-research everything, bypassing caches (costs credits)
make report            # coverage, unresolved claims, Exa usage
make path              # Earth → … → Claudia Sheinbaum Pardo, with sources and snippets
make serve             # UI + API on http://127.0.0.1:8000
make test              # unit + end-to-end tests (fake Exa client; never used in production)
```

Lower level: `cd backend && ../.venv/bin/python -m polgraph build --country MX --research-only --stages national,divisions`
(`--force` re-runs fresh stages from cached Exa responses; `--no-cache` also re-buys them; `--budget 5`).
Provincial/state assembly members: `python -m polgraph build --country PK --stages legislators --divisions PK-BA,PK-PB`.

Human review (the only way an ambiguous gate can pass):

```bash
python -m polgraph review --country MX --check contact --decision approve --reviewer "<name>" --note "checked CPEUM art. 8, LFPDPPP"
```

Monitors (Exa needs a public HTTPS webhook pointing at `/api/webhooks/exa`; signatures are verified):

```bash
python -m polgraph monitors create --country MX --webhook-url https://<public-host>/api/webhooks/exa
python -m polgraph monitors poll      # local dev: pull completed monitor runs instead of a webhook
python -m polgraph monitors process   # re-verify affected holders, mark their stages stale for the next build
```

## Cost controls

* Persistent response cache (`var/exa_cache.sqlite3`, `CACHE_TTL_HOURS`), so re-processing is free.
* Agent run IDs are persisted, so an interrupted build resumes polling instead of paying again.
* Each call reserves its worst-case cost first. Budget is enforced per build (`EXA_RUN_BUDGET_USD`) and over the
  lifetime of the ledger (`EXA_TOTAL_BUDGET_USD`); Agent runs use `effort=auto` with `budget.maxCostDollars`.
* Client-side rate limiting plus exponential-backoff retries on 429/5xx/timeouts (not on 4xx).
* Incremental refresh: stages are skipped while fresh (`TTL_*_DAYS`); monitor events mark stages stale.

## Eligibility policy (configurable in `.env`; informational unless `--production`)

* **Democracy:** every verified assessment no older than `DEMOCRACY_MAX_AGE_YEARS` must fall in the
  accepted categories, with at least `DEMOCRACY_MIN_SOURCES` of them. Disagreement or grey zone ⇒ `needs_review`.
* **Contact legality:** the worst status across rules relevant to the configured purposes and senders.
  A rule without fetchable evidence counts as `unknown`; `unknown` and `prohibited` always fail; with
  `REQUIRE_HUMAN_REVIEW=true` a pass also needs a recorded approval.

This is a research and compliance assessment, **not legal authorization**. The system never contacts anyone.

## Limitations

* National legislators are not yet populated individually (only chamber leadership); provincial legislators are,
  for Pakistan, via the opt-in `legislators` stage. Municipal coverage is the largest municipalities plus
  special jurisdictions, not every municipality.
* The Agent can misclassify *how an office is filled* (e.g. Pakistan's Prime Minister labelled "appointment").
  Verification checks who holds an office, not its selection method.
* Re-research never deletes offices that a newer run no longer reports.
* World classification uses Wikipedia's compilation tables of the indices (tier-2 evidence, labelled
  `secondary_compilation`); V-Dem has no parseable world table, so only researched countries carry V-Dem.
  Freedom House status there is the 2024 edition.
* Text verification proves co-mention (person + office + jurisdiction on the same page), not the exact
  sentence; stale pages that still list a former holder can support an outdated claim. Monitors and
  freshness TTLs are the mitigation.
* For the State of México (`MX-MEX`) the jurisdiction check is weak, because "México" appears on nearly every Mexican page.
* Democracy-label proximity in long multi-country PDFs is a heuristic.
* The EIU index is mostly paywalled, so it often can't be verified on the publisher's domain and stays `unverified`.
* The registry dataset leaves Taiwan's UN region blank; it is mapped to Asia explicitly.
* Monitors need a public HTTPS endpoint; locally use `monitors poll`.

Registry data: [lukes/ISO-3166-Countries-with-Regional-Codes](https://github.com/lukes/ISO-3166-Countries-with-Regional-Codes)
(ISO 3166-1 + UN M49) and `pycountry` (ISO 3166-2).
