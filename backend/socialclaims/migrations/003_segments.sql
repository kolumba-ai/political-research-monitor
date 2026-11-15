-- Phase 2: time-coded segments and per-person roles, derived from one raw snapshot by one processor version.
CREATE TABLE IF NOT EXISTS segment (
    segment_id         TEXT PRIMARY KEY,
    oriane_id          TEXT NOT NULL REFERENCES post(oriane_id),
    raw_id             TEXT NOT NULL REFERENCES raw_data(raw_id),
    processor_version  TEXT NOT NULL,
    source             TEXT NOT NULL CHECK (source IN ('speech', 'onscreen', 'caption', 'comment')),
    ordinal            INTEGER NOT NULL,
    text               TEXT NOT NULL,
    language           TEXT,
    text_en            TEXT,
    start_s            REAL,
    end_s              REAL,
    commented_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS segment_post ON segment (oriane_id, raw_id, processor_version);

CREATE TABLE IF NOT EXISTS post_processing (
    oriane_id            TEXT NOT NULL REFERENCES post(oriane_id),
    raw_id               TEXT NOT NULL REFERENCES raw_data(raw_id),
    processor_version    TEXT NOT NULL,
    uploader_profile_id  TEXT,
    uploader_handle      TEXT,
    audio_platform_id    TEXT,
    canonical_oriane_id  TEXT NOT NULL,      -- earliest post sharing the same original speech audio
    low_information      BOOLEAN NOT NULL,   -- no speech and no on-screen text: caption-only extraction
    segment_counts       JSONB NOT NULL,
    processed_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (oriane_id, raw_id, processor_version)
);

CREATE TABLE IF NOT EXISTS post_person_role (
    oriane_id          TEXT NOT NULL REFERENCES post(oriane_id),
    raw_id             TEXT NOT NULL REFERENCES raw_data(raw_id),
    processor_version  TEXT NOT NULL,
    person_id          TEXT NOT NULL,
    role               TEXT NOT NULL CHECK (role IN ('account_owner', 'coauthor', 'mentioned', 'quoted', 'speaker')),
    status             TEXT NOT NULL CHECK (status IN ('verified', 'probable', 'unknown')),
    evidence           JSONB NOT NULL DEFAULT '[]',
    assigned_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (oriane_id, raw_id, processor_version, person_id, role)
);
CREATE INDEX IF NOT EXISTS post_person_role_person ON post_person_role (person_id, role, status);
