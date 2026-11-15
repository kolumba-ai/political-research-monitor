# Global Political Knowledge Graph (Exa-powered)

An evidence-grounded knowledge graph of political structures and officeholders, rooted at **Earth →
Continents → Countries → Government levels → Bodies → Offices → People**. Nothing about government
structure or officeholders is hard-coded: every fact is researched live with Exa, validated against a
strict schema, and **independently re-checked against page text fetched with Exa** before it can enter the
graph. Every persisted claim links to the sources that support it.

Researched so far: **Mexico** and **Pakistan** (government structure and verified officeholders; for Pakistan also
individual provincial-assembly members via the opt-in `legislators` stage), plus a **regime classification for all 249 countries**.

The new [Political World Explorer](frontend-3d/README.md) runs at
**http://127.0.0.1:8000/explorer/**: an interactive 3D globe, country information and a native
Cytoscape/ELK government graph, all connected to the existing API. The original graph remains at `/`.
Country image discovery and the Discover/Refresh UI are pending; portraits use neutral placeholders.

## Scientific basis: why we start with politicians, social media and the media

This project collects **evidence**, not verdicts. The final judgement on what the public believes, and how
far that belief follows the evidence, is made with **polls and other survey methods**. The systems in this
repository build the measured side of that comparison.

### The starting point: the least-trusted sources of public claims

The CEVIPOF *Baromètre de la confiance politique*, wave 17 (Sciences Po / OpinionWay, February 2026)[^cevipof],
asked French registered voters how much they trust 22 organisations (question Q25; n = 3,166, online
quota sample, fieldwork 12–28 January 2026, margin of error at most 0.7–1.7 points). The three organisations
that put claims about politics into public circulation come **last**:

| Organisation (France, January 2026) | Trust ("very" + "somewhat") | Change vs. Feb 2025 |
| --- | --- | --- |
| Artisans (highest) | 84% | +2 |
| Science | 82% | +2 |
| … | … | … |
| The media | 29% | −2 |
| Social networks (Facebook, X, etc.) | 15% | −3 |
| Political parties | 15% | −1 |

Source: CEVIPOF barometer, wave 17, p. 40. On the same page, 49% say they have *no trust at all* in social
networks and 42% none at all in political parties.

The pattern holds in Mexico, where our first pilots run, but not identically. INEGI's ENCOAP 2025 (published
16 April 2026)[^encoap] finds **political parties** to be the least-trusted social and political actor it
measures: 33.7% have high or moderately high trust, compared with 75.9% for the armed forces. **The media
do better in Mexico** (51.9% in ENCOAP). Trust in *news* specifically is low, though: 36% in the Reuters
Institute Digital News Report 2025[^dnr]. We therefore do not assume that the media are equally distrusted
everywhere; we measure it country by country.

### Collective claims

We call the set of statements made **by and about a politician** across these three channels the
**collective claims** about that politician:

1. **Politicians and parties:** what officeholders say about their own record, plans and opponents
   (System A identifies who holds which office; System B extracts their statements).
2. **Social media:** what is posted about them, by them or by others, on TikTok, Instagram, YouTube, X and
   Facebook (System B, via Oriane).
3. **The media:** the news coverage that repeats, amplifies or disputes those claims (System C).

These are the channels the public trusts least, and so the ones where the gap between what is claimed and
what is supported by evidence is most worth measuring. Social media also matters because false statements
there have been shown to spread farther and faster than true ones[^vosoughi].

### From claims to evidence to polls

| Step | Question | Where |
| --- | --- | --- |
| 1. Who | Who holds which office, with sources? | System A (knowledge graph) |
| 2. What is claimed | Which checkable claims are made about each politician, when, and by whom? | System B (social claims) |
| 3. What the evidence says | Which documents (news, official statistics, government, academic, think-tank and fact-check sources) support, contradict or fail to address each claim, as known at each point in time? | System C (evidence graph) |
| 4. What the public believes | Do people believe the claims? Does belief follow the evidence, the source, or the volume of coverage? | **Polls and other survey methods (to be designed)** |

Step 4 follows an established line of research. Agenda-setting studies compare how much the media cover
each issue with what people tell pollsters is most important, and find strong correlations[^agenda].
We extend that comparison from *issues* to *claims*: alongside how much each claim is repeated, we record
whether it is supported or contradicted by evidence, so polls can test whether public belief follows the
evidence or the repetition.

### What this does not claim

* The barometer measures **trust in institutions**, not whether their claims are true. Low trust is our
  reason to study these channels, not evidence that their claims are false.
* France and Mexico differ (notably on the media), and we have not yet sourced comparable data for every
  country in the graph. Each country needs its own trust baseline before results are compared.
* The pipeline produces evidence and timestamps. It does not decide what is true for the public; that is
  what the polls are for.

[^cevipof]: CEVIPOF – Sciences Po and OpinionWay, *En quoi les Français ont-ils confiance aujourd'hui ? Le
    baromètre de la confiance politique*, vague 17, February 2026, p. 40.
    <https://www.sciencespo.fr/cevipof/sites/sciencespo.fr.cevipof/files/Barometre_confiance_CEVIPOFVague17_fev2026_vd1.pdf>
[^encoap]: INEGI, *Encuesta Nacional de Confianza en la Administración Pública (ENCOAP) 2025*, press release,
    16 April 2026. <https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/encoap/ENCOAP2025_RR.pdf>
[^dnr]: Reuters Institute for the Study of Journalism, *Digital News Report 2025*, Mexico country page.
    <https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2025/mexico>
[^vosoughi]: Vosoughi, S., Roy, D. and Aral, S. (2018). The spread of true and false news online.
    *Science*, 359(6380), 1146–1151. <https://doi.org/10.1126/science.aap9559>
[^agenda]: McCombs, M. E. and Shaw, D. L. (1972). The agenda-setting function of mass media. *Public Opinion
    Quarterly*, 36(2), 176–187. <https://doi.org/10.1086/267990>

> **Building or extending a country?** Follow [AGENTS.md](AGENTS.md) first: derive the structure from the
> country's constitution, get it reviewed, and only then fetch politicians.

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

## Official directories (opt-in stage)

`--stages directories` ingests complete official rosters fetched with Exa Contents and parsed deterministically.
The roster page is the evidence (authority tier 1), so there's no Agent cost. For Mexico this covers:
all 500 federal deputies (300 district + 200 proportional list) and 128 senators (64 majority, 32 first minority,
32 list), plus every municipality (2,459, plus Mexico City's 16 boroughs) from the INAFED register, each labelled
party election or *usos y costumbres* (customary assembly). After loading, a roster is **reconciled**:
anyone no longer on it is set to `current: false`, with history kept.

## Person matching and repair

People are matched on surname structure (given names → paternal → maternal), never on shared given names alone,
and nobody can hold two legislature seats at once. So namesakes in different assemblies stay separate.
`python -m polgraph repair-people --country MX` regroups each person's holdings by the names originally claimed
and splits any wrong merges. It's idempotent.

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

Requirements: Python 3.11+, Java 17+ (for Neo4j), and Node.js/npm to build the new explorer.
Node v24.22.2 is installed locally in `.local/node-v24.22.2-linux-x64/`; the Make targets use it.
The original UI loads React, htm and Cytoscape from CDNs and needs no Node build.
An Exa API key is required for new research; viewing an existing graph does not spend Exa credits.

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

### Run the 3D explorer

From the repository root, with the backend dependencies and `.env` already configured:

```bash
export PATH="$PWD/.local/node-v24.22.2-linux-x64/bin:$PATH"
npm --prefix frontend-3d ci
make explorer-build
make neo4j-start                  # for GRAPH_BACKEND=neo4j; skip for SQLite or an already running server
# make import-kb                  # only if you need to load the saved snapshot into an empty graph
make serve
```

Open **http://127.0.0.1:8000/explorer/**. Build before starting the server so FastAPI mounts `/explorer`.
For development, keep `make serve` running and run `make explorer-dev` in another terminal; Vite serves
**http://127.0.0.1:5173/** and proxies `/api` to the backend. `make explorer-check` runs lint and a typed
production build. See [frontend-3d/README.md](frontend-3d/README.md) for architecture and browser checks.

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
make explorer-build    # typed production build served at /explorer/ after starting the API
make explorer-dev      # frontend dev server on http://127.0.0.1:5173/; API must also be running
make explorer-check    # lint + typed production build
make sc-pull           # export System B's social claims from past.dev for /api/claims and the explorer
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

* Mexico: 688 popularly elected officials are populated (President, all 628 federal legislators, 32 governors,
  16 borough mayors, 10 municipal presidents). Not yet: ~1,098 state deputies, current presidents of the other
  ~2,449 municipalities (the official INAFED register lists only expired 2021–2024 terms), ~15,700 trustees and
  councillors, and elected federal judges. Federal deputies' party affiliation is not on the roster page and is left empty.
* Pakistan: provincial assemblies are populated except Sindh (paused).
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
