-- Run in the same transaction right after intake.sql, with the proposition IDs it returned as %(seen)s text[].
-- Queued rows (in the politician scope) whose proposition no longer has any current exact claim are skipped.
UPDATE ev_proposition_queue
SET status = 'skipped', skip_reason = 'no_current_claims', updated_at = now()
WHERE status = 'queued'
  AND NOT (proposition_id = ANY(%(seen)s::text[]))
  AND (%(politicians)s::text[] IS NULL OR politician_ids && %(politicians)s::text[]);
