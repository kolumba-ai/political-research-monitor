# Knowledge Graph Structure

What each level of the graph holds. Counts below are from the Mexico build (`run-MX-20261114T230338Z`).
Pakistan follows the same structure; its provincial assemblies are also populated member by member.

## Hierarchy at a glance

```text
Earth                                         1 node        conceptual root
└── Continent                                 7 nodes       seven-continent model
    └── Country                             249 nodes       ISO 3166-1 registry (Mexico researched)
        ├── DemocracyAssessment               3 nodes       one per index (Freedom House, V-Dem, EIU)
        ├── ContactRule                       1 node        contact / verification legality
        └── GovernmentLevel (tier)                          National · State · Municipal
            ├── GovernmentLevel (sub-unit)   59 nodes       32 states, 10 municipalities, Mexico City + 16 boroughs
            └── GovernmentBody              100 nodes       executive, legislature, court, council…
                └── PoliticalOffice         113 nodes       President, Governor of Jalisco, Borough Mayor…
                    ├── Person               63 nodes       current officeholder(s)
                    │   └── PoliticalParty    6 nodes       party membership
                    └── Term                 63 nodes       one holding of an office, with dates + evidence
                        └── Source          389 nodes       web pages that support the claim
```

Every claim-bearing node (assessment, contact rule, office, term) links to the web pages that support it
through `SUPPORTED_BY → Source`.

## What each level holds

### 1. Earth
| Holds | Example |
| --- | --- |
| `name`, `description` | "Earth — conceptual root of the political knowledge graph" |

Fixed root, created without Exa. ID: `earth`.

### 2. Continent
| Holds | Example |
| --- | --- |
| `name` | North America |

Derived from UN M49 regions: Northern America, Central America and the Caribbean map to *North America*.
ID: `continent:north-america`.

### 3. Country
| Group | Properties | Example (Mexico) |
| --- | --- | --- |
| Registry (no Exa) | `name`, `iso2`, `iso3`, `m49`, `continent`, `un_region`, `un_sub_region`, `un_intermediate_region`, `registry_source` | MX / MEX / 484, Americas › Latin America › Central America |
| Research status | `research_status` (`not_researched`, `enriched`, `research_only_enriched`), `last_verified` | research_only_enriched |
| Eligibility | `democracy_gate`, `contact_gate` (status + reasons + per-source verdicts), `eligible`, `eligibility_evaluated_at` | democracy *fail*, contact *fail*, eligible = false |
| Government | `system_of_government`, `constitution_name`, `constitution_url` | Federal presidential republic |
| Regime class (all 249 countries) | `regime_group` (Democratic / Partly democratic / Authoritarian / Unclassified), `regime_class`, `regime_basis`, `regime_by_source`, `regime_sources_agree`, `regime_classified_at` | Partly democratic — Hybrid regime (EIU 2025); indices agree |

ID: `country:MX`. Every country exists in the graph and carries a regime classification; only researched countries have a
government structure. Eligibility results are informational: they never hide data.

### 4a. DemocracyAssessment (child of Country)
| Holds | Example |
| --- | --- |
| `index`, `classification` (canonical), `classification_raw`, `grey_zone` | vdem, electoral_autocracy, "Electoral Autocracy", grey_zone = true (`EA+`) |
| `score`, `score_scale`, `assessment_year`, `publication` | 58 / 100, 2026, Freedom in the World 2026 |
| `evidence_url`, `evidence_quote` | freedomhouse.org/country/mexico/freedom-world/2026 |
| `verification`: `verified` (confirmed on the publisher's site), `unverified`, or `secondary_compilation` (from the world index tables); `checked_urls`, `agent_citations`, `compilation_url` | Freedom House and V-Dem verified; EIU from compilation |

ID: `assessment:MX:freedom-house:2026`.

### 4b. ContactRule (child of Country)
| Holds | Example |
| --- | --- |
| `status` (aggregate: allowed / restricted / prohibited / unknown), `status_reasons` | restricted |
| `dimensions` — one rule per purpose × channel × sender × recipient, each with status, conditions, legal basis, evidence URL | 24 rules |
| `laws` — statutes considered, with category (constitutional, privacy, election, lobbying, communications) | 10 laws (e.g. Constitution art. 8, LFPDPPP) |
| `review_status`, `review`, `gate`, `disclaimer`, `relevant_policy` | pending_human_review |

ID: `contactrule:MX`. This is a research and compliance assessment, not legal authorization.

### 5. GovernmentLevel (child of Country, and of other levels)
The `tier` property says which kind of level it is:

| `tier` | Count | What it represents | Extra properties |
| --- | --- | --- | --- |
| `national` | 1 | National / Federal government | — |
| `state` | 1 | State / Provincial government (container) | `name_local`, `unit_count`, `description` |
| `municipal` | 1 | Municipal / Local government (container) | `unit_name`, `unit_count`, `council_name` |
| `state_unit` | 32 | One state (incl. Mexico City) | `iso_3166_2` (e.g. MX-JAL), `iso_type`, `division_type`, `special_status_note` |
| `municipal_unit` | 10 | One municipality | `parent_division`, `parent_iso_3166_2` |
| `special_local` | 1 | Jurisdiction with distinct local governance (Mexico City boroughs) | `special_status`, `why_distinct`, `subunit_name_local` |
| `special_local_unit` | 16 | One borough (alcaldía) | — |

IDs: `level:MX:national`, `level:MX:state:MX-JAL`, `level:MX:municipal:MX-JAL:guadalajara`,
`level:MX:municipal:MX-CMX:iztapalapa`.

### 6. GovernmentBody (child of a GovernmentLevel)
| Holds | Example |
| --- | --- |
| `name`, `name_local`, `branch` (executive, legislative, judicial, electoral, autonomous, other), `description` | Senate of the Republic, legislative |
| `template` = true for the generic structure shared by all municipalities | "Municipal council — standard structure" |

ID: `body:MX:state:MX-JAL:executive`.

### 7. PoliticalOffice (child of a GovernmentBody)
| Holds | Example |
| --- | --- |
| `name` (English title), `title_local` | President of Mexico / Presidente de los Estados Unidos Mexicanos |
| `selection_method` — **how the office is filled** | direct_election (72), mixed (33), indirect_election (5), appointment (3) |
| `is_elected` (derived from `selection_method`, not from who may run), `selection_details` | true, "plurality, single round" |
| `seats`, `single_seat`, `term_length_years` | 1, true, 6 |
| `keywords` (used to check sources), `structure_evidence` | ["president", "presidenta"], verified |

ID: `office:MX:national:presidency-…:president-of-mexico`.

Legislatures with researched members have one office per seat type: `seat_type` = `general` (constituency seats,
usually `direct_election`), `women_reserved`, `minority_reserved` or `other_reserved` (usually
`indirect_election`, by party list). The legislature body carries `total_seats`, `general_seats`,
`reserved_women`, `reserved_minorities`, `constituency_range`, `official_members_url` and `members_verified`.

### 8. Person (linked to PoliticalOffice by `HOLDS_OFFICE`)
| On the node | On the `HOLDS_OFFICE` edge |
| --- | --- |
| `name` (longest verified form, titles like "Mr."/"Barrister" removed), `aliases`, `country`, `last_verified` | `current` (true/false), `status` (current/former/disputed), `start`, `end`, `is_acting`, `evidence_status`, `confidence`, `pipeline_mode`, `term_id`; for legislators also `constituency_code`, `constituency_name`, `seat_type`, `by_election` |

ID: `person:MX:claudia-sheinbaum-pardo`. Name variants merge into one node
(*Claudia Sheinbaum* = *Claudia Sheinbaum Pardo*).

Only claims that passed verification are stored:

| `evidence_status` | Meaning | Mexico count |
| --- | --- | --- |
| `verified` | 2 or more independent websites support it | 61 |
| `official_single` | one official source (gov, legislature, court, electoral body) | 1 |
| `single_source` | one non-official source (hidden in the UI by default) | 1 |
| `unsupported` | no fetched page supports it; **never stored**, logged as an unresolved claim | 0 |

### 9. PoliticalParty (linked from Person by `MEMBER_OF_PARTY`)
| On the node | On the edge |
| --- | --- |
| `name`, `acronym`, `aliases`, `country` | `coalition`, `as_of`, `term_id` |

ID: `party:MX:morena` (keyed on the acronym, so "Morena" and "Movimiento Regeneración Nacional" are one node).

### 10. Term (child of PoliticalOffice via `HAS_TERM`, linked to Person via `TERM_HELD_BY`)
| Holds | Example |
| --- | --- |
| `start`, `end`, `is_acting`, `current`, `office`, `person` | 2024-10-01 → 2030-09-30 |
| `evidence_status`, `confidence`, `last_verified`, `pipeline_mode`, `pipeline_run` | verified, 0.99 |

The Term is where an office-holding's evidence is attached (`Term → SUPPORTED_BY → Source`).

### 11. Source (evidence)
| On the node | On the `SUPPORTED_BY` edge |
| --- | --- |
| `url`, `name` (page title), `domain`, `authority_tier` (1 official, 2 reference/press, 3 other), `published_date`, `retrieved_at`, `retrieved_via` | `via` (agent_citation, corroboration_search, index_search), `claim`, `snippet` (quoted text from the page), `checked_at` |

ID: `source:<hash of canonical URL>`, so the same page is one node however many claims it supports.

## Relationships

| Relationship | From → To | Count |
| --- | --- | --- |
| `CONTAINS` | Earth → Continent | 7 |
| `CONTAINS` | Continent → Country | 249 |
| `CONTAINS` | GovernmentLevel → GovernmentLevel | 59 |
| `HAS_ASSESSMENT` | Country → DemocracyAssessment | 3 |
| `HAS_CONTACT_RULE` | Country → ContactRule | 1 |
| `HAS_GOVERNMENT_LEVEL` | Country → GovernmentLevel | 3 |
| `HAS_BODY` | GovernmentLevel → GovernmentBody | 100 |
| `HAS_OFFICE` | GovernmentBody → PoliticalOffice | 113 |
| `HOLDS_OFFICE` | Person → PoliticalOffice | 63 |
| `CONTESTS_OFFICE` | Person → PoliticalOffice (candidates) | 0, supported but not yet researched |
| `MEMBER_OF_PARTY` | Person → PoliticalParty | 59 |
| `HAS_TERM` | PoliticalOffice → Term | 63 |
| `TERM_HELD_BY` | Term → Person | 63 |
| `SUPPORTED_BY` | Term / Office / Assessment / ContactRule / Level → Source | 579 |

## Metadata shared by researched nodes

| Property | Meaning |
| --- | --- |
| `id` | Canonical ID; writes are upserts, so re-runs update rather than duplicate |
| `first_seen`, `updated_at` | When the node was created and last written |
| `last_verified` | When its evidence was last checked |
| `evidence_status`, `confidence` | Strength of supporting evidence (0–0.99) |
| `pipeline_mode` | `production` or `research_only` (data from a country that has not passed the eligibility checks) |
| `pipeline_run`, `agent_run_id` | Which pipeline run and which Exa Agent run produced it |

## Example path

```text
Earth
 └─ North America                                   Continent
     └─ Mexico                                      Country (not eligible; research-only)
         └─ National / Federal government           GovernmentLevel, tier=national
             └─ Presidency and federal executive…   GovernmentBody, branch=executive
                 └─ President of Mexico             PoliticalOffice, direct_election, 6-year term
                     └─ Claudia Sheinbaum Pardo     Person — HOLDS_OFFICE current, verified, 0.99
                         ├─ Morena                  PoliticalParty (coalition: Sigamos Haciendo Historia)
                         └─ Term 2024-10-01 → 2030-09-30
                             └─ 7 Sources: presidenta.gob.mx, diputados.gob.mx (inauguration transcript), Wikipedia…
```

Run `make path` to print this path with its sources, or open
`http://127.0.0.1:8000/#node=person%3AMX%3Aclaudia-sheinbaum-pardo` in the UI.
