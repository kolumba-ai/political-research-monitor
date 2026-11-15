-- Claim enrichment (`evidencegraph enrich`): one stance per proposition x fetched document version, judged on the page
-- text we fetched ourselves (never on Exa text). `summary` and `rationale` are the model's own words.
CREATE TABLE IF NOT EXISTS ev_evidence_link (
    link_id         TEXT PRIMARY KEY,
    proposition_id  TEXT NOT NULL,
    document_id     TEXT NOT NULL REFERENCES ev_document(document_id),
    content_sha256  TEXT NOT NULL,
    query_row       TEXT NOT NULL,
    stance          TEXT NOT NULL CHECK (stance IN ('supports', 'contradicts', 'context', 'irrelevant')),
    confidence      REAL NOT NULL,
    summary         TEXT NOT NULL,
    quote           TEXT,
    quote_verified  BOOLEAN NOT NULL,
    rationale       TEXT NOT NULL,
    mapper_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    usage           JSONB NOT NULL DEFAULT '{}',
    assessed_at     TIMESTAMPTZ NOT NULL,
    UNIQUE (proposition_id, document_id, content_sha256, mapper_version)
);
CREATE INDEX IF NOT EXISTS ev_evidence_link_proposition ON ev_evidence_link(proposition_id);

-- What was sent to past.dev: it has no route that lists data points, so every id, ingestion and content hash is kept.
CREATE TABLE IF NOT EXISTS ev_memory_record (
    datapoint_id      TEXT PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind IN ('claim', 'document', 'evidence_link')),
    proposition_id    TEXT,
    document_id       TEXT,
    politician_id     TEXT,
    past_project      TEXT NOT NULL,
    audience          TEXT,
    ingestion_id      TEXT,
    status            TEXT NOT NULL CHECK (status IN ('queued', 'completed', 'failed')),
    content_sha256    TEXT NOT NULL,
    content_bytes     INTEGER NOT NULL,
    memory_timestamp  TIMESTAMPTZ NOT NULL,
    sent_at           TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ,
    deleted_at        TIMESTAMPTZ,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS ev_memory_record_ingestion ON ev_memory_record(past_project, status, ingestion_id);
CREATE INDEX IF NOT EXISTS ev_memory_record_proposition ON ev_memory_record(proposition_id);
