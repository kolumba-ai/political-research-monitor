-- What `push-pastdev` sent to past.dev: past.dev has no route that lists data points, so every id, its ingestion and
-- its content hash are kept here. One data point per post x politician, holding that pair's current claims.
CREATE TABLE IF NOT EXISTS memory_record (
    datapoint_id       TEXT PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('post_claims')),
    oriane_id          TEXT NOT NULL REFERENCES post(oriane_id),
    politician_id      TEXT NOT NULL,
    claim_ids          TEXT[] NOT NULL,
    extractor_version  TEXT NOT NULL,
    past_project       TEXT NOT NULL,
    audience           TEXT NOT NULL,
    ingestion_id       TEXT,
    status             TEXT NOT NULL CHECK (status IN ('queued', 'completed', 'failed')),
    content_sha256     TEXT NOT NULL,
    content_bytes      INTEGER NOT NULL,
    memory_timestamp   TIMESTAMPTZ NOT NULL,
    sent_at            TIMESTAMPTZ NOT NULL,
    completed_at       TIMESTAMPTZ,
    deleted_at         TIMESTAMPTZ,
    error              TEXT
);
CREATE INDEX IF NOT EXISTS memory_record_politician ON memory_record (politician_id, status);
CREATE INDEX IF NOT EXISTS memory_record_ingestion ON memory_record (ingestion_id);
