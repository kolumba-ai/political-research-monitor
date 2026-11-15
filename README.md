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

## Principles

Five principles guide product decisions.

1. **Evidence before intuition.** Every recommendation to a politician rests on an academic finding and on
   data from the local context, and shows both.
2. **Context rules.** A finding proven in another country is not applied in Mexico until it has been tested
   here.
3. **Honesty about uncertainty.** We show trends with their margin of error, and we never present
   social-media mentions as voting intention.
4. **Public, aggregate data only.** We measure the public conversation; we do not profile or target
   individual voters.
5. **AI does the work, experts decide.** AI reads and classifies at scale; political scientists decide which
   knowledge goes in and validate how it is measured.

Source: the team's product document, translated from the Spanish original below.

<details>
<summary>Original (español)</summary>

**Principios.** Cinco principios guían las decisiones de producto.

1. **Evidencia antes que intuición.** Cada recomendación al político se apoya en un hallazgo académico y en
   datos del contexto, y muestra ambos.
2. **El contexto manda.** Un hallazgo probado en otro país no se aplica en México hasta que se prueba aquí.
3. **Honestidad sobre la incertidumbre.** Mostramos tendencias con su margen de error y nunca presentamos
   menciones en redes como intención de voto.
4. **Solo datos públicos y agregados.** Medimos la conversación pública; no perfilamos ni segmentamos
   votantes individuales.
5. **La IA trabaja, el experto decide.** La IA lee y clasifica a escala; los politólogos deciden qué
   conocimiento entra y validan cómo se mide.

</details>

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

### Across OECD countries

The OECD tracks the same pattern internationally. In its words, "democracies require citizen trust in
government to function effectively"[^oecd-topic]. Its *Survey on Drivers of Trust in Public Institutions*
(the OECD Trust Survey) is run in each country on a representative adult sample, using the same 0–10
question, where 6–10 counts as "high or moderately high" trust.

| Finding | Figure | Edition |
| --- | --- | --- |
| Trust in the national government, average across countries | 40% high or moderately high, 43% low or none | 2026 results: 33 OECD countries, including Mexico, plus 5 accession candidates; fieldwork autumn 2025[^oecd2026] |
| People who feel they have a say in what government does | 31%, while 68% believe voting influences what government does | 2026 results[^oecd2026] |
| Trust gap between people who feel they have a say and those who do not | 47 percentage points | 2026 results[^oecd2026] |
| Trust in **political parties** | 24%, below parliament (37%) and the national government (39%) | 2024 results: 30 countries, fieldwork late 2023[^oecd2024] |
| Trust in the **news media** | 39% | 2024 results[^oecd2024] |
| People who find government statistics trustworthy and verifiable | About a third | 2024 results[^oecd2024] |
| People who believe their government uses the best available evidence in decisions | 41% | 2024 results[^oecd2024] |

Three things follow for this project:

* **Parties and media are low-trust across the OECD, not only in France.** The 2024 edition's country notes
  for France, for example, list political parties (18%), news media (33%) and parliament (33%) as the least
  trusted institutions there[^oecd2024].
* **Official figures are not trusted by default either.** Only about a third of people find government
  statistics trustworthy and verifiable, so System C does not treat a government source as settling a claim
  on its own. It records which source said what, and whether independent sources agree.
* **People do not feel heard.** With only 31% feeling they have a say, polls that ask people what they
  believe are part of the answer, not a formality. This is the reason step 4 below exists.

The pattern holds in Mexico, where our first pilots run, but not identically. INEGI's ENCOAP is Mexico's
adaptation of the OECD Trust Survey[^encoap2023], so its figures are comparable with the table above.
ENCOAP 2025 (published 16 April 2026)[^encoap] finds **political parties** to be the least-trusted social and political actor it
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

### The perception gap: what people think others believe

More in Common's *Perception Gap* study[^pgap] shows why the loudest public claims are a poor guide to
what people actually think, and gives us a method for measuring the difference. With YouGov, it surveyed
2,100 Americans in the week after the 2018 midterm elections, asking each respondent two things: what they
believe, and what share of the other party they think holds each view. The **perception gap** is the
difference between that estimate and the share who actually hold the view.

What it found, and why it matters here:

| Finding (United States, 2018) | What it means for this project |
| --- | --- |
| Democrats and Republicans thought 55% of their opponents held extreme views; the real figure was about 30%. | Public perception of "the other side" can be roughly double reality. Collective claims can create that distortion, so we measure the claims, not just the reality. |
| People who follow the news "most of the time" were almost three times less accurate than those who follow it "only now and then". | Media volume is not a proxy for understanding. We record how often a claim is repeated *separately* from whether the evidence supports it. |
| Only 26% post about politics on social media, and their average gap was 29 points against 18 for those who don't post. | Social media over-represents the people with the most distorted views. Mentions are not public opinion (principle 3), and we never treat them as a sample of the electorate. |
| The most partisan groups had the widest gaps; the politically disengaged were about three times more accurate. | The voices that produce the most claims are the least representative. Polls must reach the people who stay out of the conversation. |
| People with wider gaps were more likely to describe opponents as "hateful", "ignorant" or "bigoted". | The gap is not harmless: distorted perceptions go with hostility, which is why measuring it is worth doing. |

We borrow the **method**, not the conclusions. The study is American and from 2018; under principle 2 none
of its findings is assumed to hold in Mexico until tested here. In our polls (step 4 below), we will ask
respondents both what they believe about a claim and what they think *others* believe, so that three things
can be compared for every claim: what is said (Systems B and C), what the evidence supports (System C), and
what people believe, as well as what they think others believe (polls).

### From claims to evidence to polls

| Step | Question | Where |
| --- | --- | --- |
| 1. Who | Who holds which office, with sources? | System A (knowledge graph) |
| 2. What is claimed | Which checkable claims are made about each politician, when, and by whom? | System B (social claims) |
| 3. What the evidence says | Which documents (news, official statistics, government, academic, think-tank and fact-check sources) support, contradict or fail to address each claim, as known at each point in time? | System C (evidence graph) |
| 4. What the public believes | Do people believe the claims? Does belief follow the evidence, the source, or the volume of coverage? How far is what people think others believe from what those others actually believe (the perception gap)? | **Polls and other survey methods (to be designed)** |

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
[^pgap]: Yudkin, D., Hawkins, S. and Dixon, T. (2019). *The Perception Gap: How False Impressions are Pulling
    Americans Apart*. More in Common, June 2019. <https://perceptiongap.us/>; full report:
    <https://moreincommon.com/media/0fmblxb3/the-perception-gap.pdf>
[^oecd-topic]: OECD, *Trust in government* (topic page).
    <https://www.oecd.org/en/topics/sub-issues/trust-in-government.html>
[^oecd2026]: OECD (2026), *OECD Survey on Drivers of Trust in Public Institutions 2026 Results: Navigating
    Rising Expectations and New Horizons*, OECD Publishing, Paris, 29 June 2026.
    <https://doi.org/10.1787/9eb63fec-en>. Figures from the OECD press release of the same date:
    <https://www.oecd.org/en/about/news/press-releases/2026/06/trust-steadies-but-people-still-do-not-feel-heard-oecd-trust-survey.html>
[^oecd2024]: OECD (2024), *OECD Survey on Drivers of Trust in Public Institutions – 2024 Results: Building
    Trust in a Complex Policy Environment*, OECD Publishing, Paris. <https://doi.org/10.1787/9a20554b-en>.
    Figures from the OECD summary *Building Trust in Action: Countries' Experiences* (April 2025) and the 2024
    country note for France.
[^encoap2023]: INEGI, *National Survey of Trust in Public Administration (ENCOAP) 2023*, programme page.
    <https://en.www.inegi.org.mx/programas/encoap/2023>

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

## Appendix A: Frequently asked questions

**How is this different from a social-listening tool?**
Those tools count mentions and measure whether they are positive or negative, for brands. We apply
political-science categories, such as image attributes, rejection, issue ownership and authenticity, and
turn them into recommendations.

**Is it really "real time"?**
The application is: we read the campaign conversation daily. The research is not, because an academic
article takes months or years to be published. We use the most recent academic work available.

**Does social media predict the vote?**
No. Mentions are not voting intention. That is why we calibrate against polls and show trends with their
margin of error.

**Why don't we do the scraping ourselves?**
It violates the platforms' terms and puts the client at risk. We use sources with access agreements.

**What happens if an academic finding does not hold in Mexico?**
We record it. That record is exactly the accumulated learning that forms our advantage.

**Why isn't hiring political scientists enough?**
Because political scientists already lead political analysis at many consultancies. What is new is the
system that brings their knowledge to the real context continuously and learns from every application.

**What does the product not do?**
It does not profile individual voters, does not design messages targeted at groups of voters, and does not
publish anything on the politician's behalf.

Source: the team's product document (Appendix A), translated from the Spanish original below.

<details>
<summary>Original (español)</summary>

**Apéndice A: preguntas frecuentes**

**¿En qué se diferencia de una herramienta de escucha social?** Esas herramientas cuentan menciones y miden
si son positivas o negativas, para marcas. Nosotros aplicamos categorías de ciencia política, como atributos
de imagen, rechazo, dueño del tema o autenticidad, y las convertimos en recomendaciones.

**¿De verdad es "en tiempo real"?** La aplicación sí: leemos la conversación de la campaña a diario. La
investigación no, porque un artículo tarda meses o años en publicarse. Usamos lo más reciente de la academia.

**¿Las redes predicen el voto?** No. Las menciones no son intención de voto. Por eso calibramos con encuestas
y mostramos tendencias con margen de error.

**¿Por qué no hacemos el scraping nosotros?** Viola los términos de las plataformas y pone en riesgo al
cliente. Usamos fuentes con acuerdos de acceso.

**¿Qué pasa si un hallazgo académico no se cumple en México?** Lo registramos. Ese registro es justo el
aprendizaje acumulado que forma nuestra ventaja.

**¿Por qué no basta con contratar politólogos?** Porque los politólogos ya dirigen el análisis político en
muchas consultoras. Lo nuevo es el sistema que lleva su conocimiento al contexto real de forma continua y
aprende de cada aplicación.

**¿Qué no hace el producto?** No perfila votantes individuales, no diseña mensajes dirigidos a grupos de
votantes y no publica nada en nombre del político.

</details>
