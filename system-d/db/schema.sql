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
