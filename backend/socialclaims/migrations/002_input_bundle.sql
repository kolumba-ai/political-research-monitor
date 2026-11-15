-- System A -> B input bundles (country + politician + connecting subgraph), stored once per content hash.
CREATE TABLE system_a_bundle (
    sha256        TEXT PRIMARY KEY,
    country_id    TEXT NOT NULL,
    politician_id TEXT NOT NULL,
    format        INTEGER NOT NULL,
    bundle        JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX system_a_bundle_politician ON system_a_bundle(politician_id);
ALTER TABLE politician_ref ADD COLUMN bundle_sha256 TEXT REFERENCES system_a_bundle(sha256);
