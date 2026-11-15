-- System B Phase 0 + Phase 1 schema. All timestamps UTC.

CREATE TABLE IF NOT EXISTS politician_ref (
    politician_id      TEXT NOT NULL,
    person_updated_at  TIMESTAMPTZ NOT NULL,
    version            INTEGER NOT NULL,
    snapshot_sha256    TEXT NOT NULL,
    graph_run          TEXT,
    country_code       CHAR(2) NOT NULL,
    pipeline_mode      TEXT NOT NULL CHECK (pipeline_mode IN ('production', 'research_only')),
    name               TEXT NOT NULL,
    aliases            JSONB NOT NULL DEFAULT '[]',
    offices            JSONB NOT NULL DEFAULT '[]',
    parties            JSONB NOT NULL DEFAULT '[]',
    evidence_status    TEXT,
    tracking_status    TEXT NOT NULL,
    snapshot           JSONB NOT NULL,
    query_json         JSONB,
    query_id           TEXT,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at      TIMESTAMPTZ,
    PRIMARY KEY (politician_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS politician_ref_current ON politician_ref (politician_id) WHERE superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS person_id_redirect (
    old_id      TEXT PRIMARY KEY,
    new_id      TEXT NOT NULL,
    reason      TEXT,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reconciliation_job (
    job_id         BIGSERIAL PRIMARY KEY,
    politician_id  TEXT NOT NULL,
    kind           TEXT NOT NULL,          -- identity_changed | id_missing | pipeline_mode_changed
    from_version   INTEGER,
    to_version     INTEGER,
    diff           JSONB NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS politician_account (
    account_id         BIGSERIAL PRIMARY KEY,
    politician_id      TEXT NOT NULL,
    platform           TEXT NOT NULL,
    handle             TEXT NOT NULL,
    oriane_profile_id  TEXT,
    status             TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'verified', 'rejected')),
    profile            JSONB,
    sources            JSONB NOT NULL DEFAULT '[]',
    approved_by        TEXT,
    notes              TEXT,
    proposed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_verified_at  TIMESTAMPTZ,
    last_verified_at   TIMESTAMPTZ,
    UNIQUE (politician_id, platform, handle)
);

CREATE TABLE IF NOT EXISTS run (
    run_id             TEXT PRIMARY KEY,
    politician_id      TEXT NOT NULL,
    person_updated_at  TIMESTAMPTZ NOT NULL,
    politician_version INTEGER NOT NULL,
    graph_run          TEXT,
    pipeline_mode      TEXT NOT NULL,
    query_id           TEXT NOT NULL,
    query_json         JSONB NOT NULL,
    date_from          DATE NOT NULL,
    date_to            DATE NOT NULL,
    platforms          TEXT[] NOT NULL,
    projection         TEXT NOT NULL,
    max_posts          INTEGER NOT NULL,
    excluded_ids       INTEGER NOT NULL DEFAULT 0,
    request_ids        TEXT[] NOT NULL DEFAULT '{}',
    aggregations       JSONB,
    total_count        INTEGER,
    next_offset        INTEGER NOT NULL DEFAULT 0,
    rows_returned      INTEGER NOT NULL DEFAULT 0,
    credits_used       INTEGER NOT NULL DEFAULT 0,
    branch_counts      JSONB NOT NULL DEFAULT '{}',
    status             TEXT NOT NULL DEFAULT 'running',
    error              TEXT,
    manifest           JSONB NOT NULL DEFAULT '{}',
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS raw_data (
    raw_id             TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES run(run_id),
    oriane_id          TEXT NOT NULL,
    platform           TEXT NOT NULL,
    projection         TEXT NOT NULL,
    request_id         TEXT,
    payload_path       TEXT,
    payload_sha256     TEXT NOT NULL,
    excluded_fields    TEXT[] NOT NULL DEFAULT '{}',
    pipeline_mode      TEXT NOT NULL,
    published_at       TIMESTAMPTZ NOT NULL,
    oriane_created_at  TIMESTAMPTZ,
    oriane_updated_at  TIMESTAMPTZ,
    captured_at        TIMESTAMPTZ NOT NULL,
    retain_until       TIMESTAMPTZ,
    purged_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS raw_data_oriane ON raw_data (oriane_id, captured_at);

CREATE TABLE IF NOT EXISTS post (
    oriane_id              TEXT PRIMARY KEY,
    platform               TEXT NOT NULL,
    platform_id            TEXT,
    format                 TEXT,
    url                    TEXT,
    profile_id             TEXT,
    profile_handle         TEXT,
    caption                TEXT,
    hashtags               TEXT[] NOT NULL DEFAULT '{}',
    languages              TEXT[] NOT NULL DEFAULT '{}',
    location_country_code  TEXT,
    audio_platform_id      TEXT,
    matched_queries        TEXT[] NOT NULL DEFAULT '{}',
    relevance_score        REAL,
    pipeline_mode          TEXT NOT NULL,
    latest_raw_id          TEXT REFERENCES raw_data(raw_id),
    published_at           TIMESTAMPTZ NOT NULL,
    first_observed_at      TIMESTAMPTZ NOT NULL,
    last_captured_at       TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS run_post (
    run_id           TEXT NOT NULL REFERENCES run(run_id),
    oriane_id        TEXT NOT NULL REFERENCES post(oriane_id),
    politician_id    TEXT NOT NULL,
    matched_queries  TEXT[] NOT NULL DEFAULT '{}',
    relevance_score  REAL NOT NULL,
    relevance        TEXT NOT NULL CHECK (relevance IN ('keep', 'review', 'low')),
    relevance_basis  JSONB NOT NULL DEFAULT '{}',
    raw_id           TEXT NOT NULL REFERENCES raw_data(raw_id),
    PRIMARY KEY (run_id, oriane_id)
);
CREATE INDEX IF NOT EXISTS run_post_politician ON run_post (politician_id, oriane_id);

CREATE TABLE IF NOT EXISTS post_metrics (
    oriane_id                     TEXT NOT NULL REFERENCES post(oriane_id),
    captured_at                   TIMESTAMPTZ NOT NULL,
    raw_id                        TEXT REFERENCES raw_data(raw_id),
    views                         BIGINT,
    likes                         BIGINT,
    shares                        BIGINT,
    comments                      BIGINT,
    interactions                  BIGINT,
    engagement_rate_per_views     DOUBLE PRECISION,
    engagement_rate_per_followers DOUBLE PRECISION,
    views_rate_per_followers      DOUBLE PRECISION,
    post_age_days                 DOUBLE PRECISION,
    PRIMARY KEY (oriane_id, captured_at)
);

-- Every Oriane call, billed or not; spend is computed from rows returned (Oriane has no balance endpoint).
CREATE TABLE IF NOT EXISTS oriane_usage (
    usage_id     BIGSERIAL PRIMARY KEY,
    run_id       TEXT,
    endpoint     TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    projection   TEXT NOT NULL,
    http_status  INTEGER NOT NULL,
    request_id   TEXT,
    rows         INTEGER NOT NULL DEFAULT 0,
    credits      INTEGER NOT NULL DEFAULT 0,
    called_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review (
    review_id    BIGSERIAL PRIMARY KEY,
    item_type    TEXT NOT NULL,       -- politician | post | account | reconciliation
    item_id      TEXT NOT NULL,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    payload      JSONB NOT NULL DEFAULT '{}',
    decision     TEXT,
    reviewer_id  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at  TIMESTAMPTZ,
    UNIQUE (item_type, item_id, reason)
);

-- Terms System B adds to System A's identity: ASR misspellings, distinctive surname, hashtags, exclusions.
CREATE TABLE IF NOT EXISTS query_term (
    politician_id  TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('variant', 'hashtag', 'exclude_variant')),
    value          TEXT NOT NULL,
    added_by       TEXT,
    note           TEXT,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (politician_id, kind, value)
);
