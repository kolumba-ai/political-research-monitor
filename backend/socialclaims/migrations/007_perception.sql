-- Perception labels (`label-perception`): how each post presents a politician, and the stance of its top comments.
CREATE TABLE IF NOT EXISTS post_presentation (
    oriane_id         TEXT NOT NULL REFERENCES post(oriane_id),
    politician_id     TEXT NOT NULL,
    labeller_version  TEXT NOT NULL,
    model             TEXT NOT NULL,
    relevant          BOOLEAN NOT NULL,          -- false: the post is not really about this politician
    presentation      TEXT NOT NULL CHECK (presentation IN ('positive', 'neutral', 'negative')),
    reason            TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    labelled_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (oriane_id, politician_id, labeller_version)
);
CREATE INDEX IF NOT EXISTS post_presentation_politician ON post_presentation (politician_id, labelled_at);

-- One row per top comment (a `segment` row with source = 'comment') and politician. Commenters are not stored.
CREATE TABLE IF NOT EXISTS comment_stance (
    segment_id        TEXT NOT NULL,
    oriane_id         TEXT NOT NULL REFERENCES post(oriane_id),
    politician_id     TEXT NOT NULL,
    labeller_version  TEXT NOT NULL,
    stance            TEXT NOT NULL CHECK (stance IN ('for', 'against', 'neutral')),
    -- bot: the same text sits under 3 or more posts; unsure: generic text with no content of its own (counts as human)
    author_kind       TEXT NOT NULL CHECK (author_kind IN ('human', 'bot', 'unsure')),
    labelled_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (segment_id, politician_id, labeller_version)
);
CREATE INDEX IF NOT EXISTS comment_stance_post ON comment_stance (oriane_id, politician_id, labeller_version);
