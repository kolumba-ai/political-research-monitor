CREATE TABLE IF NOT EXISTS ev_proposition_queue (
    proposition_id TEXT PRIMARY KEY,
    politician_ids TEXT[] NOT NULL,
    claim_types TEXT[] NOT NULL,
    topics TEXT[] NOT NULL,
    max_checkworthiness REAL NOT NULL,
    reach BIGINT NOT NULL,
    priority DOUBLE PRECISION NOT NULL,
    stated_at TIMESTAMPTZ NOT NULL,
    latest_claim_at TIMESTAMPTZ NOT NULL,
    claim_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'mapping', 'mapped', 'skipped')),
    skip_reason TEXT,
    mapper_version TEXT,
    queued_at TIMESTAMPTZ NOT NULL,
    mapped_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ev_proposition_queue_priority ON ev_proposition_queue(status, priority DESC);

CREATE TABLE IF NOT EXISTS ev_source_registry (
    domain TEXT PRIMARY KEY,
    source_class TEXT,
    publisher TEXT,
    authority_tier INTEGER NOT NULL,
    licence_class TEXT NOT NULL,
    can_store_full_text BOOLEAN NOT NULL,
    robots_status TEXT,
    crawl_delay DOUBLE PRECISION,
    feed_type TEXT,
    feed_url TEXT,
    is_claimant_for TEXT[] NOT NULL DEFAULT '{}',
    list TEXT NOT NULL DEFAULT 'allow' CHECK (list IN ('allow', 'block')),
    notes TEXT,
    added_by TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL,
    reviewed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ev_exa_request (
    request_id TEXT PRIMARY KEY,
    worker TEXT NOT NULL,
    purpose TEXT NOT NULL,
    proposition_id TEXT,
    params_hash TEXT NOT NULL,
    params JSONB NOT NULL,
    latency_ms INTEGER NOT NULL,
    result_count INTEGER NOT NULL,
    cost_usd DOUBLE PRECISION,
    requested_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ev_discovered_url (
    url_hash TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    title TEXT,
    exa_published_date TEXT,
    author TEXT,
    score DOUBLE PRECISION,
    rank INTEGER,
    discovered_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (url_hash, request_id)
);

CREATE TABLE IF NOT EXISTS ev_fetch_queue (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    priority DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    last_error TEXT,
    enqueued_at TIMESTAMPTZ NOT NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ev_fetch_queue_ready ON ev_fetch_queue(status, next_attempt_at, priority DESC);

CREATE TABLE IF NOT EXISTS ev_document (
    document_id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL,
    resolved_url TEXT NOT NULL,
    publisher_domain TEXT NOT NULL,
    source_class TEXT,
    authority_tier INTEGER NOT NULL,
    licence_class TEXT NOT NULL,
    can_store_full_text BOOLEAN NOT NULL,
    title TEXT,
    authors TEXT[] NOT NULL DEFAULT '{}',
    language TEXT,
    outbound_links TEXT[] NOT NULL DEFAULT '{}',
    date_origin TEXT NOT NULL,
    date_confidence TEXT NOT NULL,
    dup_cluster_id TEXT,
    fetch_status TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    modified_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ev_document_version (
    document_id TEXT NOT NULL REFERENCES ev_document(document_id),
    content_sha256 TEXT NOT NULL,
    snapshot_path TEXT,
    archive_origin TEXT,
    http_status INTEGER NOT NULL,
    bytes BIGINT NOT NULL,
    simhash BIT(64) NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    snapshot_as_of TIMESTAMPTZ,
    PRIMARY KEY (document_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS ev_document_version_latest ON ev_document_version(document_id, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS ev_kg_edge (
    edge_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ev_kg_edge_pair ON ev_kg_edge(type, src, dst);
