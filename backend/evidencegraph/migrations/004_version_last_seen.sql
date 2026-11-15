-- When a page returns to earlier content, that version becomes current again: track when each version was last seen.
ALTER TABLE ev_document_version ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
UPDATE ev_document_version SET last_seen_at = retrieved_at WHERE last_seen_at IS NULL;
