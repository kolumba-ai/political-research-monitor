-- Link System D rows to the explorer's storylines (socialclaims `storylines.json`) and System B's claims.
-- Additive only: every column is nullable, so the demo seed and existing runs are unaffected.
alter table politicians add column person_id text;          -- System A id, e.g. person:MX:claudia-sheinbaum-pardo
create unique index politicians_person_id_key on politicians (person_id);

alter table aggregated_claims add column storyline_id text;  -- sl-<hash> from storylines.json
alter table aggregated_claims add column storyline_label text;
create unique index aggregated_claims_storyline_id_key on aggregated_claims (storyline_id);

alter table claims add column source_claim_id text;          -- claim_id in the claims export / distilled_claim
create unique index claims_source_claim_id_key on claims (source_claim_id);
