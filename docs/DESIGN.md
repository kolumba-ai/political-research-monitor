# System D — Design Document

Status: ready to build · Owner: Mauricio (Kolumba AI) · Last updated: 2026-11-15

System D validates claims made by or about politicians on social media. It compares **social sentiment** (from comments and reactions) with **polled sentiment** (from conversations with a sampled population), and it keeps polling each claim only until the sample is statistically valid.

For the hackathon, respondents are **simulated** (LLM personas). The same code must later run against real respondents over WhatsApp and Vapi voice calls without structural changes.

---

## 1. Pipeline

```
Context ──► Politicians ──► Claims ──► Aggregated claims
                │                              │
                │ politician context           ▼
                └──────────────────► Polling agent fleet ◄── Question design
                                           │
                                           │ WhatsApp / voice / simulator
                                           ▼
Population ──► Sampling agent ──────────► Sample
                    ▲                      │ answers + sentiment
                    │ next batch or stop   ▼
             Stopping controller ◄──── Neon database ──► UI
```

| # | Component | Input | Output | Runs on |
|---|---|---|---|---|
| 1 | Context ingestion | Politician watchlist, news context | `politicians` rows | Modal function |
| 2 | Claim extractor | Posts, captions, video transcripts, comments | `claims`, `social_reactions` | Modal function |
| 3 | Claim aggregator | `claims` + embeddings | `aggregated_claims` (canonical, testable) | Modal function |
| 4 | Question designer | Aggregated claim + politician context | `question_sets` (Spanish script) | Modal function |
| 5 | Population model | Strata definitions + shares | `population_strata` | Seed script |
| 6 | Sampling agent | Strata quotas + controller request | `respondents` for the next batch | Modal function |
| 7 | Polling agent fleet | Respondent + question set | `poll_responses` | Modal `.map()` |
| 8 | Respondent simulator | Persona + agent messages | Persona replies (sim mode only) | Modal function |
| 9 | Stopping controller | `poll_responses` per claim | `claim_sampling_state`, next-batch request | Modal scheduled / loop |
| 10 | UI | Neon (read-only) | Dashboard | Next.js or Streamlit |

---

## 2. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Compute | **Modal** (Python) | One function per agent. Fleet uses `.map()` over the batch. Webhooks via `@modal.fastapi_endpoint`. |
| Database | **Neon Postgres + pgvector** | Use the **pooled** connection string (`-pooler` host) stored as Modal Secret `neon-db` (`DATABASE_URL`). One Neon branch per simulation run is optional. |
| LLM | Anthropic / OpenAI API | Secret `llm-api` (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`). |
| Embeddings | 1536-dim model | Store in `vector(1536)`; change dimension in schema if a different model is used. |
| Voice | **Vapi** | Outbound calls; transcript via webhook. Secret `vapi`. Phase 2. |
| Text | **WhatsApp** (Meta Cloud API or Twilio) | Template message to open the conversation. Secret `whatsapp`. Phase 2. |
| UI | Next.js or Streamlit | Reads Neon only. |

**Channel abstraction (required):** the polling agent talks to a `Channel` interface with three implementations — `SimulatorChannel`, `WhatsAppChannel`, `VapiChannel`. Only `SimulatorChannel` is required for the MVP.

```python
class Channel(Protocol):
    def start(self, respondent: Respondent, opening: str) -> str: ...      # returns conversation_id
    def send(self, conversation_id: str, message: str) -> str | None: ...  # returns reply (sync) or None (async/webhook)
    def close(self, conversation_id: str) -> None: ...
```

---

## 3. Data model (Neon)

```sql
create extension if not exists vector;
create extension if not exists pgcrypto;

create table politicians (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  party text,
  region text,
  context jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

create table claims (
  id uuid primary key default gen_random_uuid(),
  politician_id uuid references politicians(id),
  claim_text text not null,
  source_platform text,              -- tiktok | x | facebook | youtube
  source_url text,
  embedding vector(1536),
  aggregated_claim_id uuid,          -- set by aggregator
  created_at timestamptz default now()
);

create table aggregated_claims (
  id uuid primary key default gen_random_uuid(),
  politician_id uuid references politicians(id),
  canonical_text text not null,
  claim_count int default 1,
  testable boolean default true,
  embedding vector(1536),
  created_at timestamptz default now()
);

alter table claims
  add constraint claims_aggregated_fk
  foreign key (aggregated_claim_id) references aggregated_claims(id);

create table social_reactions (
  id uuid primary key default gen_random_uuid(),
  claim_id uuid references claims(id),
  platform text,
  comment_text text,
  sentiment real check (sentiment between -1 and 1),
  stance text check (stance in ('believes','disputes','neutral')),
  created_at timestamptz default now()
);

create table question_sets (
  id uuid primary key default gen_random_uuid(),
  aggregated_claim_id uuid references aggregated_claims(id),
  version int not null default 1,
  language text default 'es-MX',
  script jsonb not null,             -- {opening, belief_q, sentiment_q, open_q, closing}
  created_at timestamptz default now()
);

create table population_strata (
  id uuid primary key default gen_random_uuid(),
  region text, age_band text, gender text, urban_rural text,
  share real not null                -- shares sum to 1.0
);

create table respondents (
  id uuid primary key default gen_random_uuid(),
  stratum_id uuid references population_strata(id),
  is_simulated boolean not null default true,
  persona jsonb,                     -- sim mode: generated persona
  phone_e164 text,                   -- real mode only
  channel text check (channel in ('simulator','whatsapp','voice')),
  created_at timestamptz default now()
);

create table poll_responses (
  id uuid primary key default gen_random_uuid(),
  aggregated_claim_id uuid references aggregated_claims(id),
  respondent_id uuid references respondents(id),
  question_set_id uuid references question_sets(id),
  sim_run text not null,             -- e.g. 'sim-run-3' or 'live-2026-11-20'
  batch_no int not null,
  channel text,
  transcript jsonb,
  sentiment real check (sentiment between -1 and 1),
  believes_claim boolean,
  completed boolean default false,
  created_at timestamptz default now()
);

create table claim_sampling_state (
  aggregated_claim_id uuid references aggregated_claims(id),
  sim_run text,
  n_completed int default 0,
  p_believe real, ci_low real, ci_high real,
  sentiment_mean real, sentiment_ci_low real, sentiment_ci_high real,
  social_sentiment real,
  status text check (status in ('polling','converged','decisive','capped')) default 'polling',
  updated_at timestamptz default now(),
  primary key (aggregated_claim_id, sim_run)
);

create index on poll_responses (aggregated_claim_id, sim_run);
create index on claims using hnsw (embedding vector_cosine_ops);
create index on aggregated_claims using hnsw (embedding vector_cosine_ops);
```

**Sentiment scale:** every sentiment score, social or polled, is a float in **[-1, 1]** toward the claim's subject, so the gap is directly comparable.

---

## 4. Stopping rule

Polling runs in batches. After each batch, the controller recomputes each claim's statistics and decides one of four outcomes.

Sample size ceiling for a proportion at 95% confidence: `n = 1.96² · p(1−p) / e²` → worst case (p = 0.5): ≈97 at ±10 pts, ≈196 at ±7 pts, ≈385 at ±5 pts.

**Config (env or `config.py`):**

| Param | Default | Meaning |
|---|---|---|
| `SEED_BATCH` | 30 | First batch per claim |
| `NEXT_BATCH` | 20 | Each later batch |
| `TARGET_HALF_WIDTH` | 0.07 | Converged when the 95% CI half-width on `p_believe` ≤ this |
| `MAX_CALLS` | 400 | Hard cap per claim per run |
| `MIN_PER_STRATUM` | 5 | Never stop before every stratum has this many completes |

**Algorithm (per claim, per run):**

1. Seed batch: `SEED_BATCH` respondents, allocated across strata in proportion to `share`.
2. After each batch compute:
   - `p_believe` = post-stratified mean of `believes_claim` (weight = stratum share / stratum sample share), with a **Wilson 95% interval**.
   - `sentiment_mean` with a 95% t-interval.
3. If any stratum has fewer than `MIN_PER_STRATUM` completes → continue (step 6).
4. Stop with a status:
   - `decisive` — sentiment CI excludes `social_sentiment` (the gap is established).
   - `converged` — `(ci_high − ci_low)/2 ≤ TARGET_HALF_WIDTH`.
   - `capped` — `n_completed ≥ MAX_CALLS` (UI shows "inconclusive").
5. Stopped claims marked `capped` are queued for a **question rewrite** (new `question_sets.version`); responses for different versions are analysed separately.
6. Otherwise, request `NEXT_BATCH` respondents, allocated to the strata **furthest below quota** first.

Implement as a pure function so it can be unit-tested:

```python
def decide(responses: list[Response], strata: list[Stratum], social_sentiment: float, cfg: Config) -> Decision:
    """Returns Decision(status, next_batch_allocation: dict[stratum_id, int], stats)."""
```

---

## 5. Agents — prompts and contracts

All LLM outputs are **JSON** validated with Pydantic.

- **Claim extractor** → `[{claim_text, about_politician, is_factual_claim, source_url}]`. Ignore opinions and insults. Keep only checkable statements.
- **Social sentiment scorer** → per comment `{sentiment: -1..1, stance: believes|disputes|neutral}`.
- **Claim aggregator** → cosine similarity > 0.90 merges into an existing `aggregated_claim`. Otherwise a new one is created. The LLM writes `canonical_text` and `testable`.
- **Question designer** → Spanish (es-MX), neutral wording, ≤ 4 questions, ≤ 90 seconds spoken. Must include: a belief question (yes / no / don't know), a sentiment question (5-point scale mapped to -1..1), and one open question.
- **Persona generator** → from a stratum: `{name, age, state, municipality_type, occupation, education, media_diet, political_leaning, trust_in_institutions, speaking_style}`. Personas must be varied within a stratum.
- **Respondent simulator** → plays the persona and answers in character. It may refuse, misunderstand or give short answers, at a realistic rate of about 10–15%.
- **Polling agent** → runs the script, handles off-topic replies and refusals, and never argues. It returns `{believes_claim, sentiment, completed, transcript}`.

---

## 6. Repo layout

```
system-d/
├── README.md
├── docs/DESIGN.md
├── pyproject.toml
├── .env.example
├── db/
│   ├── schema.sql
│   └── seed/                 # politicians.json, claims.json, strata.json
├── system_d/
│   ├── config.py
│   ├── db.py                 # psycopg pool, helpers
│   ├── models.py             # Pydantic models
│   ├── llm.py                # LLM + embeddings wrapper
│   ├── channels/
│   │   ├── base.py
│   │   ├── simulator.py
│   │   ├── whatsapp.py       # phase 2
│   │   └── vapi.py           # phase 2
│   ├── agents/
│   │   ├── claim_extractor.py
│   │   ├── aggregator.py
│   │   ├── question_designer.py
│   │   ├── sampling.py
│   │   ├── polling.py
│   │   └── persona.py
│   └── stopping.py           # pure decide() + stats
├── modal_app.py              # Modal app: functions, .map() fleet, webhooks, run_simulation entrypoint
├── ui/                       # dashboard
└── tests/
    ├── test_stopping.py
    └── test_aggregator.py
```

---

## 7. Build order (milestones for Devin)

1. **M1 — Data:** `db/schema.sql` and a migration script. Seed 2–3 politicians, about 10 claims and 12–20 Mexican strata.
2. **M2 — Stopping logic:** `stopping.py` with full unit tests (Wilson interval, post-stratification, all four statuses, quota allocation).
3. **M3 — Claims:** extractor, social scorer and aggregator working on the seed data.
4. **M4 — Simulation loop:** question designer, personas, `SimulatorChannel`, polling fleet on Modal and the controller loop. Run with `modal run modal_app.py::run_simulation --sim-run sim-run-1 --claims all`.
5. **M5 — UI:** claims table (canonical claim, social sentiment, polled sentiment, gap, p_believe ± CI, n, status) and a transcript drill-down.
6. **M6 — Real channels (stretch):** `VapiChannel` and `WhatsAppChannel` with webhook endpoints. One real end-to-end test call.

**Definition of done (MVP):** one command runs a full simulation over all seeded claims. Every claim reaches a terminal status, and the UI shows the social vs. polled gap with confidence intervals.

---

## 8. Constraints and guardrails

- `is_simulated` and `sim_run` are always set. The UI labels simulated results clearly.
- Real mode: a consent line at the start of every conversation, opt-out honoured immediately, calls only between 9:00 and 20:00 local time, and phone numbers never shown in the UI or logs.
- No secrets in the repo. Use `.env.example` and Modal Secrets only.
- LLM calls get retries with backoff. Fleet concurrency is configurable (default 50).

## 9. Open questions

- Real population shares: is there an INEGI-based strata table, or are estimates enough for the demo?
- Final precision target for the demo (±7 or ±10 points).
- Social data source: scraper, official API, or a hand-curated seed set for the hackathon.
