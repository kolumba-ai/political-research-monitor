# Structure Spec: Mexico (MX)

Status: **APPROVED 2026-11-15** (AGENTS.md Step 4); see Decisions.

## Source

| | |
| --- | --- |
| Constitution | *Constitución Política de los Estados Unidos Mexicanos* (CPEUM) |
| Official text | https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf (Cámara de Diputados, Leyes Federales Vigentes) |
| Version | **TEXTO VIGENTE, última reforma publicada DOF 07-10-2026** |
| Retrieved | 2026-11-15, via Exa Contents; every article below was read from this text, not from memory |
| Recent reforms that affect structure | Judicial election (DOF 15-09-2024; art. 94 amended again DOF 02-06-2026); art. 115-I ayuntamiento composition (current wording: one sindicatura, up to fifteen regidurías) |

## Structure (as defined by the constitution)

```text
Estados Unidos Mexicanos: representative, democratic, secular, federal Republic of free and sovereign
States and Mexico City, united in a federation (art. 40)
│
├── FEDERATION: Supremo Poder de la Federación, divided into Legislative, Executive, Judicial (art. 49)
│   │
│   ├── LEGISLATIVE: Congreso General, two chambers (art. 50)
│   │   ├── Chamber of Deputies (Cámara de Diputados): renewed every 3 years; one alternate (suplente) per deputy (art. 51)
│   │   │   ├── 300 deputies by relative majority in single-member districts (art. 52)
│   │   │   └── 200 deputies by proportional representation, regional lists in plurinominal circumscriptions (art. 52)
│   │   └── Senate (Cámara de Senadores): 128 (art. 56)
│   │       ├── 64 = 2 per entity by relative majority (art. 56)
│   │       ├── 32 = 1 per entity assigned to the first minority (art. 56)
│   │       └── 32 by proportional representation, one national list (art. 56)
│   │   (Presiding officers, i.e. Mesa Directiva presidents: separate offices held by members, chosen by each chamber)
│   │
│   ├── EXECUTIVE: a single individual, "Presidente de los Estados Unidos Mexicanos" (art. 80)
│   │   ├── President: direct election (art. 81); takes office 1 October, 6 years, no re-election (art. 83)
│   │   └── Secretaries of State (Cabinet): appointed by the President (not elected)
│   │
│   ├── JUDICIAL: Poder Judicial de la Federación (art. 94)
│   │   ├── Supreme Court of Justice (SCJN): 9 ministers, Pleno and two sections; presidency rotates every
│   │   │   2 years to the most-voted candidates (art. 94)
│   │   ├── Electoral Tribunal: Superior Chamber + regional chambers (art. 94; elected per art. 96)
│   │   ├── Regional Plenaries (Plenos Regionales) (art. 94)
│   │   ├── Collegiate Circuit Courts (Tribunales Colegiados de Circuito) (art. 94)
│   │   ├── Collegiate Appeals Courts (Tribunales Colegiados de Apelación) (art. 94)
│   │   ├── District Courts (Juzgados de Distrito) (art. 94)
│   │   ├── Judicial Discipline Tribunal (Tribunal de Disciplina Judicial) (art. 94)
│   │   └── Judicial administration body (órgano de administración judicial) (art. 94)
│   │   → Ministers, Electoral Tribunal magistrates (superior and regional), Discipline Tribunal magistrates,
│   │     circuit magistrates and district judges are elected "de manera libre, directa y secreta por la
│   │     ciudadanía" (art. 96)
│   │
│   └── AUTONOMOUS CONSTITUTIONAL BODIES
│       └── National Electoral Institute (INE): General Council = 1 consejero presidente + 10 consejeros
│           electorales (art. 41-V-A); appointed through the Chamber of Deputies (not popular vote)
│
└── FEDERAL ENTITIES: 31 States + Mexico City (art. 43); sovereign in internal regime (arts. 40, 41)
    │
    ├── STATE (×31), e.g. Jalisco: powers divided into Executive, Legislative, Judicial (art. 116)
    │   ├── Executive: Governor, direct election, ≤ 6 years, subject to recall (art. 116-I)
    │   ├── Legislative: State Legislature (Congreso local), direct election (art. 116-I), relative majority +
    │   │   proportional representation; size proportional to population, minimum 7 / 9 / 11 deputies (art. 116-II)
    │   ├── Judicial: courts established by each state constitution (art. 116-III)
    │   └── MUNICIPALITIES OF THE STATE: the "Municipio" (art. 115)
    │       └── Municipality, e.g. Guadalajara
    │           └── Ayuntamiento (municipal council), "de elección popular directa" (art. 115-I):
    │               ├── Municipal President (Presidente/a Municipal): 1
    │               ├── Trustee (Sindicatura): 1
    │               └── Councillors (Regidurías): up to 15 (exact number set by state law)
    │
    └── MEXICO CITY (Ciudad de México): federal entity, seat of the Powers of the Union and capital (art. 44)
        ├── Executive: Head of Government (Jefatura de Gobierno), direct election (art. 122-A)
        ├── Legislative: Legislature of Mexico City, relative majority + proportional representation,
        │   direct election (art. 122-A)
        ├── Judicial: Superior Court of Justice, local Judicial Discipline Tribunal, administration body (art. 122-A-IV)
        └── ALCALDÍAS (demarcaciones territoriales): number and limits set by the local constitution (art. 122-A-VI)
            └── Alcaldía (×16), e.g. Iztapalapa
                ├── Mayor (Alcalde/Alcaldesa): elected by "votación universal, libre, secreta y directa", 3 years
                └── Council (Concejo): Concejales elected on the same slate (planilla of 7–10) (art. 122-A-VI-a)

SPECIAL REGIMES
• Indigenous peoples and communities elect their own authorities "de acuerdo con sus sistemas normativos"
  (art. 2-A); municipalities with indigenous population elect representatives to their ayuntamientos (art. 2).
  → e.g. Oaxaca municipalities governed under sistemas normativos internos ("usos y costumbres").
• Alternates (suplentes) are elected with deputies (art. 51) and with ayuntamiento/alcaldía members (art. 122).
```

## How each office is filled (graph `selection_method`)

| Office | Method | Article |
| --- | --- | --- |
| President | `direct_election` | 81 |
| Federal deputy, district | `direct_election` (relative majority) | 52 |
| Federal deputy, PR | `direct_election` via party list (proportional representation) | 52 |
| Senator, relative majority / first minority | `direct_election` | 56 |
| Senator, PR | `direct_election` via national party list | 56 |
| SCJN minister, Electoral and Discipline Tribunal magistrates, circuit magistrates, district judges | `direct_election` | 96 |
| Chamber / Senate presiding officers, SCJN president | `indirect_election` (by members / by vote ranking) | 94; chamber rules |
| INE councillors | `indirect_election` (via Chamber of Deputies) | 41 |
| Cabinet secretaries | `appointment` | 89 |
| Governor, state legislators | `direct_election` | 116 |
| Municipal president, síndico, regidores | `direct_election` (slate); `customary_election` where sistemas normativos apply | 115, 2 |
| Head of Government, Mexico City legislators | `direct_election` | 122 |
| Alcalde, concejales | `direct_election` (slate) | 122 |

## Delegated to ordinary law (to read before populating those tiers)

* Number of regidores per municipality: each **state constitution / municipal law** (art. 115-I: up to 15).
* State legislature size and seat split: each **state constitution** (art. 116-II minimums).
* Number of concejales per alcaldía: **Constitución Política de la Ciudad de México** (art. 122-A-VI).
* Sub-municipal authorities (juntas auxiliares, agencias, comisarías): not in the CPEUM; created by **state
  municipal laws**.
* Municipalities under sistemas normativos: each state electoral institute (for Oaxaca, IEEPCO).

## Implementing law and roster check (Step 3)

| Check | Constitution | Official roster / register | Result |
| --- | --- | --- | --- |
| Federal deputies | 300 + 200 (art. 52) | Chamber SITL list: 300 Dtto + 200 Circ | ✅ match |
| Senators | 64 + 32 + 32 (art. 56) | Senate "por principio de elección" | ✅ match |
| Federal entities | 31 + CDMX (art. 43) | ISO 3166-2 MX (32) | ✅ match |
| Municipalities | per state | INAFED register: 2,459 municipalities + 16 alcaldías | ✅ 2,475 units |
| Customary-system municipalities | art. 2 | INAFED marks 316; Oaxaca's institute reports 417 in Oaxaca alone | ⚠️ INAFED undercounts; use state electoral institutes |
| Ayuntamiento composition | 1 president + 1 síndico + ≤ 15 regidores (art. 115-I) | — | current graph has only the president office |

## Differences from the current graph (what the migration would change)

1. **Remove the country-wide "Municipal / Local government" tier.** Move each municipality under
   *Federal entities → <State> → Municipalities of <State>*; move the 16 alcaldías under *Mexico City → Alcaldías*.
2. **Rename tiers** to constitutional terms: "Federation (Federación)" and "Federal entities (Entidades federativas)".
3. **Federal judiciary bodies** (art. 94): add Regional Plenaries and Collegiate Appeals Courts; rename the
   court bodies to the article 94 list; mark the elected offices per art. 96. The administration body stays non-elected.
4. **Ayuntamiento offices** (art. 115-I): add Trustee (1) and Councillors (up to 15, exact number per state law)
   to every council, next to the Municipal President.
5. **Alcaldía offices** (art. 122): add Councillors (Concejales) next to the Alcalde.
6. **State bodies** (art. 116): keep Executive and Legislature; add the State Judiciary body (open question 4).
7. Each tier, body and office node gets an `article` property citing the CPEUM.
8. **No officeholders are deleted.** People keep their verified holdings; only their containers move.

## Open questions for the reviewer

1. Tier names: English with the official Spanish term in brackets, e.g. "Federal entities (Entidades federativas)". OK?
2. Seat types: keep separate offices per seat type (district vs PR), as now, or one office per chamber with seat type per holder?
3. Add Síndico and Regidor offices to all 2,459 ayuntamientos now (holders empty until populated)?
4. Add a State Judiciary body to each state now (offices to be defined from each state constitution later)?
5. Sub-municipal authorities: leave out for now? (They come from state laws, not the CPEUM.)
6. Suplentes (alternates): leave out for now?

## Decisions

| Date | Decision |
| --- | --- |
| 2026-11-15 | Overall structure approved: follow the CPEUM (official documentation for countries without a constitution). |
| 2026-11-15 | Tier names: English with the official Spanish term in brackets. |
| 2026-11-15 | Seat types: separate offices per seat type (district vs PR; majority / first minority / list). |
| 2026-11-15 | Add now: the full federal judiciary per art. 94. **Deferred** (in the spec, not yet created): Síndico/Regidor offices, Alcaldía concejales, state judiciaries. |
| 2026-11-15 | Left out for now: sub-municipal authorities, suplentes. |
