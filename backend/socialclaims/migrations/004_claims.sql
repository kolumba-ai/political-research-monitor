-- Phase 3: claims distilled from Phase 2 segments, grounded, clustered into strict propositions.
CREATE TABLE IF NOT EXISTS claim_extraction (
    oriane_id          TEXT NOT NULL REFERENCES post(oriane_id),
    raw_id             TEXT NOT NULL REFERENCES raw_data(raw_id),
    extractor_version  TEXT NOT NULL,
    politician_id      TEXT NOT NULL,
    model              TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    claims             INTEGER NOT NULL DEFAULT 0,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    error              TEXT,
    extracted_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (oriane_id, raw_id, extractor_version, politician_id)
);

CREATE TABLE IF NOT EXISTS proposition (
    proposition_id   TEXT PRIMARY KEY,      -- hash of the normalized key fields below
    canonical_text   TEXT NOT NULL,
    subject          TEXT NOT NULL,
    predicate        TEXT NOT NULL,
    negated          BOOLEAN NOT NULL,
    vals             JSONB NOT NULL DEFAULT '[]',
    place            TEXT,
    period_start     DATE,
    period_end       DATE,
    qualifiers       JSONB NOT NULL DEFAULT '[]',
    topic_id         TEXT,
    created_at       TIMESTAMPTZ NOT NULL,
    first_stated_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS distilled_claim (
    claim_id                TEXT PRIMARY KEY,
    oriane_id               TEXT NOT NULL REFERENCES post(oriane_id),
    raw_id                  TEXT NOT NULL REFERENCES raw_data(raw_id),
    politician_id           TEXT NOT NULL,
    segment_ids             TEXT[] NOT NULL,
    proposition_id          TEXT REFERENCES proposition(proposition_id),
    quote                   TEXT NOT NULL,
    quote_char_start        INTEGER,
    quote_char_end          INTEGER,
    text                    TEXT NOT NULL,
    text_en                 TEXT,
    speaker_id              TEXT,
    speaker_status          TEXT NOT NULL CHECK (speaker_status IN ('verified', 'probable', 'unknown')),
    speaker_evidence        JSONB NOT NULL DEFAULT '[]',
    relation_to_politician  TEXT NOT NULL CHECK (relation_to_politician IN ('speaker', 'subject', 'quoted', 'mentioned')),
    type                    TEXT NOT NULL CHECK (type IN ('factual', 'statistic', 'policy_position', 'commitment',
                                                          'forecast', 'accusation', 'opinion')),
    topics                  TEXT[] NOT NULL DEFAULT '{}',
    entities                JSONB NOT NULL DEFAULT '{}',
    checkworthiness         REAL NOT NULL,
    modality                TEXT NOT NULL CHECK (modality IN ('asserted', 'questioned', 'quoted', 'satire')),
    grounding               TEXT NOT NULL CHECK (grounding IN ('exact', 'review', 'failed')),
    grounding_issues        JSONB NOT NULL DEFAULT '[]',
    stated_at               TIMESTAMPTZ NOT NULL,
    original_utterance_at   TIMESTAMPTZ,
    quote_start_s           REAL,
    quote_end_s             REAL,
    referenced_period_start DATE,
    referenced_period_end   DATE,
    extractor_version       TEXT NOT NULL,
    extracted_at            TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS distilled_claim_post ON distilled_claim (oriane_id, extractor_version);
CREATE INDEX IF NOT EXISTS distilled_claim_politician ON distilled_claim (politician_id, grounding);
CREATE INDEX IF NOT EXISTS distilled_claim_proposition ON distilled_claim (proposition_id);
