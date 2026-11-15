# Political Research Monitor

Political Research Monitor measures the gap between three things: what is claimed by and about politicians, what
the evidence says, and what people actually believe.

It is built as four systems. **System A** maps who holds which office in every country. **System B** captures what is said
by and about each officeholder on social media. **System C** finds the evidence for and against each claim.
**System D** polls a sample of the population about the same claims. Every fact links to its sources, and the
project gives no verdicts of its own: polls decide whether belief follows the evidence.

| | |
| --- | --- |
| Explore the data | [Political World Explorer](frontend-3d/README.md), at `http://127.0.0.1:8000/explorer/` after `make serve` |
| Build a country | [AGENTS.md](AGENTS.md): constitution first, reviewed structure, then politicians |
| System docs | [A (below)](#system-a-global-political-knowledge-graph) · [B](docs/system_b/README.md) · [C](docs/system_c/README.md) · [D](https://github.com/kolumba-ai/political-research-monitor/blob/docs/system-d-design/docs/DESIGN.md) |
| Run on Modal | [docs/modal.md](docs/modal.md) |

## 1. The research need

Public claims about politics come from the sources people trust least. In the CEVIPOF barometer for France
(wave 17, January 2026, n = 3,166), the three channels that put political claims into circulation come last of
22 organisations[^cevipof]:

| Trust in France, January 2026 ("very" or "somewhat") | |
| --- | --- |
| Artisans (highest) | 84% |
| Science | 82% |
| The media | 29% |
| Social networks | 15% |
| Political parties | 15% |

The pattern holds across the OECD: **24%** trust political parties and **39%** trust the news media in 30 countries
(2024)[^oecd2024], and only **31%** feel they have a say in what government does (2026)[^oecd2026]. In Mexico, where
the first pilots run, political parties are the least-trusted actor INEGI measures (33.7%)[^encoap].

We call everything said by and about a politician through these three channels (politicians and parties, social
media, and the media) their **collective claims**. Because these are the least-trusted channels, this is where the gap
between what is claimed and what the evidence supports is most worth measuring.

## 2. Scope: every democracy, including the flawed ones

Claims and distrust matter most where institutions are weakest, so the project does not stop at full democracies.
Every country is classified, and the method applies wherever people vote.

| | |
| --- | --- |
| **249** | countries and territories classified by regime (EIU Democracy Index 2025, Freedom House as fallback) |
| **132** | in scope: 99 democracies and 33 partly democratic or hybrid regimes |
| **1,415** | officeholder records in the first two pilots, each linked to its sources |

* **Mexico** is rated a hybrid regime (EIU 2025). The President, all 628 federal legislators and 32 governors are verified.
  It is the first country for social claims, evidence and polling.
* **Pakistan** holds elections but is rated authoritarian (EIU 2025). Its provincial assemblies are loaded, except Sindh,
  which is paused. It is the stress test.

## 3. Methodology: from collective claims to evidence to polls

1. **Who holds office.** Every officeholder is checked against pages fetched from the web. A holding counts when two
   independent domains, or one official source, support it (System A).
2. **What is claimed.** Posts by and about each politician on TikTok, Instagram, YouTube, X and Facebook are captured,
   turned into claims and grouped into storylines (System B). Media coverage enters through System C.
3. **What the evidence says.** News, official statistics, government, legislative and court records, fact-checks and
   polls are mapped to each claim as supporting, contradicting or context. A quote is kept only if it is found in the
   fetched page (System C).
4. **What people believe.** A stratified sample is polled about each claim, and polled sentiment is compared with
   social-media sentiment (System D). This step borrows its method from More in Common's *Perception Gap* study:
   measure what people believe separately from what is said about them[^pgap].

## 4. Design: four systems, each with one job

```text
System A ──────────► System B ──────────► System C ──────────► System D
Knowledge graph      Social claims        Evidence graph       Polling
who holds office     what is claimed      what the evidence    what people believe
                                          says
```

| System | Job | Code | Store | Status |
| --- | --- | --- | --- | --- |
| **A** Knowledge graph | Countries, government bodies, offices and verified officeholders | `backend/polgraph/` | Neo4j (or SQLite) | Built: Mexico, Pakistan; regime classification for all 249 countries |
| **B** Social claims | Posts by and about each politician, claims, storylines by week, engagement and perception metrics | `backend/socialclaims/` | Postgres + pgvector, past.dev | Built for Mexico |
| **C** Evidence graph | Documents found online and mapped to each claim | `backend/evidencegraph/` | Postgres + past.dev | M0 skeleton and M1 enrichment built |
| **D** Polling | Polled versus social sentiment per claim, with a stopping rule | not started | Neon Postgres + pgvector, Modal | Designed, with a test plan ([design](https://github.com/kolumba-ai/political-research-monitor/blob/docs/system-d-design/docs/DESIGN.md), not yet merged) |

Each system trusts only the one before it:
* System B never creates a politician. Its only input is System A's verified bundle for that person.
* System C never keeps a quote it cannot find in the fetched page.
* System D never stops polling a claim before every population group has 5 answers.

**How System D decides when to stop.** Each claim is polled in batches: 30 respondents first, then 20 at a time. A
claim stops when it is one of these:
* **converged:** the share who believe the claim is known to within ±7 points;
* **decisive:** polled sentiment clearly differs from social-media sentiment;
* **capped:** 400 completed answers were reached, so the claim is inconclusive and its question is rewritten.

A simulation of this rule showed that repeating the decisive check after every batch would report a gap that isn't
there for 11–19% of claims. Testing that check at 99% confidence brings this to 3–5%. The System D test plan sets
out this fix and five others.

## 5. Tools

| Tool | Role | What it does |
| --- | --- | --- |
| [Exa](https://exa.ai) | Research | Finds and re-fetches the pages behind every fact; daily monitors flag elections, appointments and resignations |
| Oriane | Social capture | Captures posts on TikTok, Instagram, YouTube, X and Facebook |
| Claude (Anthropic API) | Reasoning | Plans searches, extracts claims, judges stance and storylines, labels how posts present a politician, writes poll questions |
| past.dev | Memory | Keeps each politician's claims and linked evidence for agents to recall |
| Neo4j | Data | The graph of countries, offices and officeholders |
| Neon Postgres + pgvector | Data | Claims, documents, respondents and sampling state |
| [Modal](https://modal.com) | Compute | Runs the pipelines and agents in parallel, and hosts the public read-only explorer |
| Vapi, WhatsApp | Channels | Voice and chat polling of real respondents (System D, phase 2) |
| Claude Code | Build | Research, design documents, pilots and test plans |
| Devin | Build | Builds each system from its design document |

## 6. The product: Political World Explorer

The [explorer](frontend-3d/README.md) is a React app served by the project API at `/explorer/`:

* **Globe and country panel:** every country on a 3D globe with its regime classification, democracy assessments,
  contact rules and evidence.
* **Government graph:** the country's structure from the national level down to each office and its holder, with
  the evidence status of each holding and links to its sources.
* **Politician pages:** each politician's claims, with **storylines by week** (topics as rows, each storyline with its
  versions, posts, evidence documents and links to the original posts), and a **Metrics** tab.
* **Metrics page:** engagement per politician from System B, plus a presentation index (how videos present the
  politician) and a feedback index (how top comments respond), both from −100 to +100.

```sh
make explorer-build && make serve        # http://127.0.0.1:8000/explorer/
make modal-explorer-data && make modal-explorer   # public read-only copy on Modal
```

The Modal copy has **no login**: anyone with the URL can read the graph, claims, post links, account handles and
evidence. Details are in [docs/modal.md](docs/modal.md#public-explorer).

## 7. Next steps

1. **Build System D.** Devin builds milestones M1–M5 on Modal and Neon. Each milestone ships only when it passes its
   gate in the test plan, and the stopping-rule fixes are adopted.
2. **Poll real people.** Run WhatsApp and Vapi polls in Mexico, with a consent line, opt-outs and calling hours, and
   check the results against the simulated runs.
3. **Measure the perception gap.** Ask people what they believe and what they think others believe, and compare both
   with the collective claims.
4. **Widen coverage.** Finish Mexico's state deputies and municipal presidents, then add countries that have a national
   trust survey to benchmark against.

## What this project does not claim

* It does not decide what is true. It records which source said what, and whether independent sources agree.
* It does not treat a government source as settling a claim on its own: only about a third of people across the
  OECD find government statistics trustworthy and verifiable[^oecd2024].
* While System D's respondents are simulated, it measures what a model predicts people believe, not what they believe.
  Simulated results are always labelled as simulated.
* It never contacts anyone without consent. Contact-legality checks are a research assessment, not legal authorization.

## Repository map

| Path | Contents |
| --- | --- |
| `backend/polgraph/` | System A: research stages, Exa gateway, graph store, API |
| `backend/socialclaims/` | System B: capture, claims, storylines, topics, metrics, perception labels |
| `backend/evidencegraph/` | System C: source registry, intake, fetcher, enrichment, past.dev projection |
| `frontend-3d/` | Political World Explorer (React, Vite) |
| `frontend/` | The original graph UI, served at `/` |
| `knowledge_base/` | Committed graph snapshot and research provenance |
| `structures/` | Reviewed constitutional structure specs per country |
| `docs/` | Design documents and deployment notes |

---

## System A: Global Political Knowledge Graph

An evidence-grounded knowledge graph of political structures and officeholders, rooted at **Earth →
Continents → Countries → Government levels → Bodies → Offices → People**. Nothing about government
structure or officeholders is hard-coded: every fact is researched live with Exa, validated against a
strict schema, and **independently re-checked against page text fetched with Exa** before it can enter the
graph. Every persisted claim links to the sources that support it.

Researched so far: **Mexico** and **Pakistan** (government structure and verified officeholders; for Pakistan also
individual provincial-assembly members via the opt-in `legislators` stage), plus a **regime classification for all 249 countries**.

> **Building or extending a country?** Follow [AGENTS.md](AGENTS.md) first: derive the structure from the
> country's constitution, get it reviewed, and only then fetch politicians.

### Where Exa is called (main loop)

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

### Pipeline

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

#### Claim verification

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

### Knowledge base snapshot

The complete graph and its research provenance are committed in [knowledge_base/](knowledge_base/)
(nodes, relationships, flattened CSVs, Exa Agent outputs with citations, unresolved claims, cost ledger).
Load it with `make import-kb`, with no Exa key needed; refresh it with `make export`. See
[knowledge_base/README.md](knowledge_base/README.md).

### Official directories (opt-in stage)

`--stages directories` ingests complete official rosters fetched with Exa Contents and parsed deterministically.
The roster page is the evidence (authority tier 1), so there's no Agent cost. For Mexico this covers:
all 500 federal deputies (300 district + 200 proportional list) and 128 senators (64 majority, 32 first minority,
32 list), plus every municipality (2,459, plus Mexico City's 16 boroughs) from the INAFED register, each labelled
party election or *usos y costumbres* (customary assembly). After loading, a roster is **reconciled**:
anyone no longer on it is set to `current: false`, with history kept.

### Person matching and repair

People are matched on surname structure (given names → paternal → maternal), never on shared given names alone,
and nobody can hold two legislature seats at once. So namesakes in different assemblies stay separate.
`python -m polgraph repair-people --country MX` regroups each person's holdings by the names originally claimed
and splits any wrong merges. It's idempotent.

### Regime classification (all countries)

`python -m polgraph classify-world` gives every country a `regime_group`: **Democratic**, **Partly democratic**,
**Authoritarian**, or **Unclassified** (no index covers it, mostly dependent territories). It also sets a detailed
`regime_class`, a per-index breakdown, and whether the indices agree. EIU's 2025 Democracy Index is the primary
source, because its typology (full/flawed democracy, hybrid regime, authoritarian) maps directly onto these groups;
Freedom House status is the fallback. Ratings confirmed on a publisher's own site (from the per-country
`democracy` stage) take precedence over compilation rows and are never downgraded. Country names are matched to
ISO codes exactly, never fuzzily, so Kosovo stays unmatched instead of overwriting Serbia. The classification is
informational: it does not gate or hide anything.

### Legislators (opt-in stage)

`--stages legislators [--divisions PK-PB,PK-SD]` fills in individual members of each first-level legislature:

1. One Agent run gets the assembly's composition: seat counts by type, constituency code range (e.g. PP-1…PP-297), and the official member list.
2. Members are requested in slices of 50 constituencies, plus one slice for reserved seats.
3. General seats go into a `direct_election` office. Reserved seats (women, minorities) go into their own
   `indirect_election` offices, since they're allocated by party list. Each holding records `constituency_code` and `seat_type`.
4. Each member is verified (name + assembly + constituency on a fetched page). When an official page already
   confirms a member, no extra corroboration search is run, which keeps bulk lists affordable (Balochistan, 65 seats: $1.30).

### Data model

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

### Setup

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

#### Run the 3D explorer

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

### Commands

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
make portraits         # find a verified portrait per MX politician with Exa (Person.image_url, ~$0.01 each)
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

### Cost controls

* Persistent response cache (`var/exa_cache.sqlite3`, `CACHE_TTL_HOURS`), so re-processing is free.
* Agent run IDs are persisted, so an interrupted build resumes polling instead of paying again.
* Each call reserves its worst-case cost first. Budget is enforced per build (`EXA_RUN_BUDGET_USD`) and over the
  lifetime of the ledger (`EXA_TOTAL_BUDGET_USD`); Agent runs use `effort=auto` with `budget.maxCostDollars`.
* Client-side rate limiting plus exponential-backoff retries on 429/5xx/timeouts (not on 4xx).
* Incremental refresh: stages are skipped while fresh (`TTL_*_DAYS`); monitor events mark stages stale.

### Eligibility policy (configurable in `.env`; informational unless `--production`)

* **Democracy:** every verified assessment no older than `DEMOCRACY_MAX_AGE_YEARS` must fall in the
  accepted categories, with at least `DEMOCRACY_MIN_SOURCES` of them. Disagreement or grey zone ⇒ `needs_review`.
* **Contact legality:** the worst status across rules relevant to the configured purposes and senders.
  A rule without fetchable evidence counts as `unknown`; `unknown` and `prohibited` always fail; with
  `REQUIRE_HUMAN_REVIEW=true` a pass also needs a recorded approval.

This is a research and compliance assessment, **not legal authorization**. The system never contacts anyone.

### Limitations

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

[^cevipof]: CEVIPOF – Sciences Po and OpinionWay, *En quoi les Français ont-ils confiance aujourd'hui ? Le
    baromètre de la confiance politique*, vague 17, February 2026, p. 40.
    <https://www.sciencespo.fr/cevipof/sites/sciencespo.fr.cevipof/files/Barometre_confiance_CEVIPOFVague17_fev2026_vd1.pdf>
[^encoap]: INEGI, *Encuesta Nacional de Confianza en la Administración Pública (ENCOAP) 2025*, press release,
    16 April 2026. <https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/encoap/ENCOAP2025_RR.pdf>
[^pgap]: Yudkin, D., Hawkins, S. and Dixon, T. (2019). *The Perception Gap: How False Impressions are Pulling
    Americans Apart*. More in Common, June 2019. <https://perceptiongap.us/>; full report:
    <https://moreincommon.com/media/0fmblxb3/the-perception-gap.pdf>
[^oecd2026]: OECD (2026), *OECD Survey on Drivers of Trust in Public Institutions 2026 Results: Navigating
    Rising Expectations and New Horizons*, OECD Publishing, Paris, 29 June 2026.
    <https://doi.org/10.1787/9eb63fec-en>. Figures from the OECD press release of the same date:
    <https://www.oecd.org/en/about/news/press-releases/2026/06/trust-steadies-but-people-still-do-not-feel-heard-oecd-trust-survey.html>
[^oecd2024]: OECD (2024), *OECD Survey on Drivers of Trust in Public Institutions – 2024 Results: Building
    Trust in a Complex Policy Environment*, OECD Publishing, Paris. <https://doi.org/10.1787/9a20554b-en>.
    Figures from the OECD summary *Building Trust in Action: Countries' Experiences* (April 2025) and the 2024
    country note for France.
