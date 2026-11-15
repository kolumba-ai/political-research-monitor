# Agent protocol: build each country's structure from its constitution

**Read this before researching, extending or restructuring any country in the knowledge graph.**

The graph is only useful if its hierarchy matches how each country actually governs itself. A wrong structure is a
correctness error, not a cosmetic one: it puts officials under the wrong authority, miscounts elected offices,
and makes every query built on it misleading. So:

> **Golden rule: no politician is fetched for a country until that country's structure has been derived from its
> constitution, written down as a Structure Spec with article citations, and approved by a human reviewer.**

---

## 1. The protocol

### Step 0: Find the authoritative constitutional text

If the country has **no single codified constitution** (e.g. United Kingdom, New Zealand, Israel), build the
structure from its **official government documentation**: constitutional statutes and Basic Laws, the official
government or parliament description of the system, and the electoral authority's documentation. Cite those
documents in place of articles. The same review gate applies.

* Use Exa Search restricted to official domains (official gazette, legislature, supreme court, ministry of
  justice). For example, Mexico: `diputados.gob.mx/LeyesBiblio` (consolidated CPEUM); Pakistan: `na.gov.pk`, `pakistancode.gov.pk`.
* Prefer the **current consolidated text** (with amendments). Record its URL, title and "last reform" date.
* Secondary, for orientation only: `constituteproject.org` (English translations; check its version date).
* Constitutions are amended. Search for amendments **after** the consolidated text's date (e.g. Mexico's
  2024 judicial reform, Pakistan's 2025 constitutional amendments) and note them.
* Record the sources in the Structure Spec. Nothing is taken from model memory alone.

### Step 1: Read for structure, and extract these elements
For each element, record **what the constitution says and the article number**.

| # | Element | Questions to answer |
| --- | --- | --- |
| 1 | Form of state | Federal, unitary, devolved, confederal? Who are the constituent parts? |
| 2 | Territorial divisions | First-level units (states, provinces, regions…), how many, their official names. Are some units different (capital district, federal territory, autonomous/administered area)? |
| 3 | Local government | Does the constitution create local government directly, or delegate it to the first-level units? **Local government is nested under whoever creates and governs it.** |
| 4 | Branches at each level | Executive, legislative, judicial (and autonomous constitutional bodies) at national, first-level and local level. |
| 5 | Bodies and offices | Every body (chamber, council, court) and its offices: title (official language + English), seat count, composition. |
| 6 | How each office is filled | Direct popular vote, proportional-representation list, indirect (by a legislature or council), appointment, ex officio, hereditary, customary/community assembly. Record it **per seat type** (e.g. 300 district + 200 list deputies). |
| 7 | Terms | Length, re-election rules, staggering. |
| 8 | Special regimes | Indigenous/customary self-government, special-status territories, reserved seats (women, minorities), sub-local authorities. |
| 9 | What the constitution delegates to ordinary law | e.g. council sizes set by state law, local-government acts. Note where to find those laws (Step 3). |

### Step 2: Write the Structure Spec
Use the template in section 3. Every node type you plan to create must trace to an article (or to a named
statute that the constitution delegates to). Use the **country's own terminology**: English name plus the official
term, e.g. "Federal entities (Entidades federativas)", "Municipal council (Ayuntamiento)".

### Step 3: Check the spec against implementing law and reality
* Election laws and local-government acts fill in what the constitution delegates (council sizes, local tiers).
* Check seat counts against official rosters (legislature member lists, electoral authority results).
* If the constitution and practice differ (e.g. local elections postponed, administrators appointed instead),
  record **both**: the constitutional design, and the current de facto situation with a source.

### Step 4: Human review (mandatory)
Present the Structure Spec to the user as a tree **before** building or restructuring. List open questions
explicitly (naming, granularity, what to include now vs later). Record the decisions in the spec's
*Decisions* section. **Do not restructure an existing graph without approval.**

### Step 5: Build the graph to the spec
* Create the tiers and bodies exactly as specified. Nesting follows constitutional authority (section 2).
* Only then run officeholder research and roster ingestion, each attached to the office the spec defines.

### Step 6: Validate before reporting
* Seat counts per office equal the constitutional or statutory number (or the gap is explained: vacancies, not yet researched).
* No unit sits under the wrong parent; every local unit has its first-level division as an ancestor.
* No tier exists that the constitution does not define.
* Selection methods match the spec (e.g. a prime minister elected by the legislature is `indirect_election`, not `appointment`).

---

## 2. Modelling rules

1. **Nesting follows authority.** A unit sits under the government that constitutes it. Municipalities created
   under state constitutions live inside their state; local governments created by provincial acts live inside
   their province. **Never create a country-wide "municipal" tier** for a federal or devolved country.
2. **Special-status units stay where the constitution puts them**, with a note on why they differ (e.g.
   Mexico City: a federal entity, not a state; Islamabad Capital Territory: federally administered).
3. **Offices are defined by how they are filled.** Where one chamber mixes seat types (district vs list vs
   reserved), record the seat type and its selection method explicitly; never label a mixed body with one method.
4. **Presiding officers are separate offices** (Speaker, Senate President) held by members, not merged into the member office.
5. **`is_elected` means the office is filled by voters** (directly, by list, or by customary assembly), not that its holder once ran for something.
6. **Don't invent tiers or merge tiers** for visual convenience. Display concerns (large lists) are solved in the UI (paging), not by changing the structure.
7. **Cite everything.** Each tier, body and office node carries the constitutional article (or statute) it rests on.
8. **De jure vs de facto.** If practice deviates (postponed elections, appointed administrators), model the
   constitutional office and attach the actual current holder with a note and source.

---

## 3. Structure Spec template

```yaml
country: <ISO 3166-1 alpha-2>
constitution:
  title: <official title>
  url: <official consolidated text>
  last_reform: <date>
  amendments_checked_until: <date>
form_of_state: <federal | unitary | devolved | ...>          # art. N
levels:
  - tier: national
    name: <English (official term)>                         # art. N
    bodies:
      - name: <English (official)>
        branch: <executive | legislative | judicial | autonomous>
        article: <N>
        offices:
          - title: <English (official)>
            seats: <n>
            seat_types:                                      # if mixed
              - {type: district, seats: 300, selection: direct_election, article: 52}
              - {type: proportional_list, seats: 200, selection: direct_election (party list), article: 52}
            term_years: <n>
  - tier: first_level
    name: <e.g. Federal entities (Entidades federativas)>   # art. N
    units: <count>; special_units: [<name: why different, article>]
    bodies: [...]
    local_government:                                        # nested here when the division constitutes it
      name: <e.g. Municipalities (Municipios)>
      created_by: <constitution art. N | state law>
      bodies: [...]
      sub_local: <optional: auxiliary authorities, selection varies>
special_regimes: [<customary self-government, reserved seats, ...>]
delegated_to_statute: [<what and where>]
decisions: [<date: decision taken with reviewer>]
open_questions: [...]
sources: [<url, title, date>]
```

---

## 4. Worked example: Mexico

The approved, source-verified spec is in [structures/MX.md](structures/MX.md) and supersedes the summary below.


Source: *Constitución Política de los Estados Unidos Mexicanos* (CPEUM), consolidated text at
`https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf`. Articles below are as commonly cited; **verify each
against the current consolidated text** (the 2024 judicial reform changed arts. 94–100).

```text
Mexico: federal, representative, democratic republic (art. 40)
├── Federation: powers divided into Executive, Legislative, Judicial (art. 49)
│   ├── Executive: President, elected by direct vote, 6 years (arts. 80–83)
│   ├── Legislative: Congress of the Union, two chambers (art. 50)
│   │   ├── Chamber of Deputies: 500 = 300 single-member districts + 200 proportional list (art. 52)
│   │   └── Senate: 128 = 2 majority + 1 first minority per entity (96) + 32 national list (art. 56)
│   ├── Judicial: Supreme Court, Electoral Tribunal, Judicial Discipline Tribunal, circuit and district courts;
│   │             judges elected by popular vote since the 2024 reform (arts. 94–100)
│   └── Autonomous bodies, e.g. National Electoral Institute (art. 41)
└── Federal entities: 31 states + Mexico City (art. 43)
    ├── Each state: Governor, single-chamber Congress, state judiciary (art. 116)
    │   └── Municipalities of the state: the "municipio libre" (art. 115)
    │       └── Ayuntamiento: Municipal President + Regidores + Síndicos, by direct popular election (art. 115-I);
    │                         council size set by state law
    └── Mexico City: Head of Government, Congress of Mexico City (art. 122)
        └── Alcaldías (16): Alcalde + Concejo, by popular election (art. 122)
Special regimes: indigenous peoples' own normative systems, e.g. Oaxaca's "usos y costumbres" municipalities (art. 2)
```

Open questions for review: tier naming (English + Spanish), seat types as separate offices vs per-holder
attribute, adding síndico/regidor offices before holders are known, sub-municipal authorities, state judiciaries.

## 5. Worked example: Pakistan (draft for review)

Source: *Constitution of the Islamic Republic of Pakistan, 1973*, as amended (check `pakistancode.gov.pk` and
`na.gov.pk` for the current text, including 2024–2025 amendments).

```text
Pakistan: federal republic of provinces + Islamabad Capital Territory (art. 1)
├── Federation
│   ├── President: elected by an electoral college of both Houses and the provincial assemblies (arts. 41, Second Schedule)
│   ├── Majlis-e-Shoora (Parliament): President + National Assembly + Senate (art. 50)
│   │   ├── National Assembly: general seats (direct election) + reserved seats for women and non-Muslims (art. 51)
│   │   └── Senate: elected by provincial assemblies and others (art. 59)
│   ├── Prime Minister: elected by the National Assembly (art. 91), so indirect_election, not appointment
│   └── Judiciary: Supreme Court, High Courts, and courts created by later amendments (Part VII)
├── Provinces: Punjab, Sindh, Khyber Pakhtunkhwa, Balochistan
│   ├── Governor (appointed by the President, art. 101); Chief Minister elected by the Provincial Assembly (art. 130)
│   ├── Provincial Assembly: general + reserved seats (art. 106)
│   └── Local governments: each province must establish them by provincial law (art. 140A),
│       e.g. metropolitan/municipal corporations, district and tehsil/town councils (vary by province)
├── Islamabad Capital Territory: federally administered (art. 1)
└── Gilgit-Baltistan, Azad Jammu & Kashmir: administered under separate instruments, not provinces (record their own orders/acts)
```

---

## 6. Mistakes already made (don't repeat them)

| Mistake | Why it was wrong | Rule |
| --- | --- | --- |
| A country-wide "Municipal / Local government" tier for Mexico, with all 2,459 municipalities under it | Municipalities belong to their state (CPEUM art. 115) | 2.1 nesting follows authority |
| Pakistan's Prime Minister labelled "appointment" | Elected by the National Assembly (art. 91) | 2.3 / Step 6 |
| Chamber Speaker and Deputy Speaker stored as holders of "Member of National Assembly" | Presiding officers are separate offices | 2.4 |
| Mexico's Chamber of Deputies recorded as one office, "direct election" | 200 of 500 seats are proportional list (art. 52) | 2.3 |
| Officeholders fetched before the structure was understood | Wrong containers had to be repaired later | Golden rule |

---

## 7. Checklist before fetching any politician

- [ ] Official consolidated constitution found; URL, version and amendments recorded
- [ ] Structure Spec written with an article for every tier, body and office
- [ ] Local government nested under the division that constitutes it
- [ ] Seat types and selection methods per seat recorded
- [ ] Special regimes and special-status units recorded
- [ ] Spec reviewed and approved by a human; decisions logged
- [ ] Graph tiers created to match the spec; validation (section 1, Step 6) passes
- [ ] Only now: officeholder research and roster ingestion
