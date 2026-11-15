-- System C intake: select checkable System B propositions into ev_proposition_queue.
-- Params: %(politicians)s text[] or NULL (all), %(min_cw)s real (0.5), %(mapper_version)s text.
-- Only claims from each (post, politician)'s latest successful extraction count, so older extractor runs never double count.
WITH latest AS (
    SELECT DISTINCT ON (oriane_id, politician_id) oriane_id, politician_id, extractor_version
    FROM claim_extraction
    WHERE status = 'ok'
    ORDER BY oriane_id, politician_id, extracted_at DESC
),
claims AS (
    SELECT d.proposition_id, d.politician_id, d.oriane_id, d.type, d.topics, d.checkworthiness,
           d.stated_at, d.extracted_at,
           (d.type IN ('factual', 'statistic', 'accusation')
            OR (d.type = 'commitment' AND p.period_end IS NOT NULL AND p.period_end < current_date)) AS eligible,
           (d.type = 'commitment' AND (p.period_end IS NULL OR p.period_end >= current_date)) AS commitment_pending,
           (d.type IN ('policy_position', 'forecast', 'opinion')) AS not_verifiable
    FROM distilled_claim d
    JOIN latest l ON l.oriane_id = d.oriane_id AND l.politician_id = d.politician_id
                 AND l.extractor_version = d.extractor_version
    JOIN proposition p ON p.proposition_id = d.proposition_id
    WHERE d.grounding = 'exact'
      AND d.modality IN ('asserted', 'quoted')
      AND (%(politicians)s::text[] IS NULL OR d.politician_id = ANY(%(politicians)s::text[]))
),
views AS (
    SELECT DISTINCT ON (oriane_id) oriane_id, COALESCE(views, 0) AS views
    FROM post_metrics
    ORDER BY oriane_id, captured_at DESC
),
reach AS (
    SELECT pp.proposition_id, SUM(COALESCE(v.views, 0))::bigint AS reach
    FROM (SELECT DISTINCT proposition_id, oriane_id FROM claims) pp
    LEFT JOIN views v ON v.oriane_id = pp.oriane_id
    GROUP BY pp.proposition_id
),
topics AS (
    SELECT c.proposition_id, array_agg(DISTINCT t ORDER BY t) AS topics
    FROM claims c, unnest(c.topics) AS t
    GROUP BY c.proposition_id
),
per_prop AS (
    SELECT c.proposition_id,
           array_agg(DISTINCT c.politician_id ORDER BY c.politician_id) AS politician_ids,
           array_agg(DISTINCT c.type ORDER BY c.type) AS claim_types,
           MAX(c.checkworthiness) FILTER (WHERE c.eligible) AS max_cw_eligible,
           MAX(c.checkworthiness) AS max_cw_any,
           bool_or(c.commitment_pending) AS any_commitment_pending,
           bool_or(c.not_verifiable) AS any_not_verifiable,
           MIN(c.stated_at) AS stated_at,
           MAX(c.extracted_at) AS latest_claim_at,
           COUNT(*) AS claim_count
    FROM claims c
    GROUP BY c.proposition_id
),
selected AS (
    SELECT pp.proposition_id, pp.politician_ids, pp.claim_types,
           COALESCE(t.topics, '{}') AS topics,
           COALESCE(pp.max_cw_eligible, pp.max_cw_any) AS max_checkworthiness,
           r.reach,
           COALESCE(pp.max_cw_eligible, pp.max_cw_any) * ln(1 + r.reach) AS priority,
           pp.stated_at, pp.latest_claim_at, pp.claim_count,
           CASE WHEN pp.max_cw_eligible >= %(min_cw)s THEN 'queued' ELSE 'skipped' END AS status,
           CASE WHEN pp.max_cw_eligible >= %(min_cw)s THEN NULL
                WHEN pp.max_cw_eligible IS NOT NULL THEN 'below_checkworthiness'
                WHEN pp.any_commitment_pending THEN 'commitment_deadline_pending'
                WHEN pp.any_not_verifiable THEN 'not_currently_verifiable'
           END AS skip_reason
    FROM per_prop pp
    JOIN reach r USING (proposition_id)
    LEFT JOIN topics t USING (proposition_id)
)
INSERT INTO ev_proposition_queue AS q
    (proposition_id, politician_ids, claim_types, topics, max_checkworthiness, reach, priority,
     stated_at, latest_claim_at, claim_count, status, skip_reason, queued_at, updated_at)
SELECT proposition_id, politician_ids, claim_types, topics, max_checkworthiness, reach, priority,
       stated_at, latest_claim_at, claim_count, status, skip_reason, now(), now()
FROM selected
ON CONFLICT (proposition_id) DO UPDATE SET
    politician_ids = EXCLUDED.politician_ids,
    claim_types = EXCLUDED.claim_types,
    topics = EXCLUDED.topics,
    max_checkworthiness = EXCLUDED.max_checkworthiness,
    reach = EXCLUDED.reach,
    priority = EXCLUDED.priority,
    stated_at = EXCLUDED.stated_at,
    latest_claim_at = EXCLUDED.latest_claim_at,
    claim_count = EXCLUDED.claim_count,
    status = CASE
        WHEN q.status = 'mapping' THEN 'mapping'
        WHEN EXCLUDED.status = 'skipped' THEN 'skipped'
        WHEN q.status = 'mapped' AND q.mapper_version = %(mapper_version)s
             AND EXCLUDED.latest_claim_at <= q.mapped_at THEN 'mapped'
        ELSE 'queued' END,
    skip_reason = CASE WHEN q.status <> 'mapping' AND EXCLUDED.status = 'skipped'
                       THEN EXCLUDED.skip_reason ELSE NULL END,
    queued_at = CASE WHEN q.status <> 'queued' AND q.status <> 'mapping' AND EXCLUDED.status = 'queued'
                      AND NOT (q.status = 'mapped' AND q.mapper_version = %(mapper_version)s
                               AND EXCLUDED.latest_claim_at <= q.mapped_at)
                     THEN now() ELSE q.queued_at END,
    updated_at = now()
RETURNING proposition_id, status, skip_reason;
