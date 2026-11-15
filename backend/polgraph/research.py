"""Exa research stages. Each stage:
  1. asks Exa Agent for a schema-constrained, cited extraction (POST /agent/runs);
  2. validates the structured output with Pydantic (bad items dropped, never patched);
  3. independently re-fetches the cited pages with Exa Contents (POST /contents) and, when
     corroboration is thin, runs targeted Exa Searches (POST /search) for more independent sources;
  4. returns claims annotated with an evidence status — the pipeline decides what is persisted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import ValidationError

from .config import Settings
from .evidence import (EvidenceStatus, SupportingSource, Verification, authority_tier, classify, evaluate_pages,
                       merge_supporting, office_keywords)
from .exa_gateway import ExaGateway, grounding_urls, urls_for_item
from .ids import canonical_url, normalize_text, registrable_domain
from .logging_setup import get_logger
from .registry import CountryRecord, subdivisions
from .schemas import (ContactX, DemocracyX, DivisionsX, LocalStructureX, NationalStructureX, OfficeholderX,
                      SpecialJurisdictionsX, _LABELS, agent_schema, normalize_classification)

log = get_logger("research")

INDEX_DOMAINS = {
    "freedom_house": ["freedomhouse.org"],
    "vdem": ["v-dem.net", "ourworldindata.org"],
    "eiu": ["eiu.com", "economist.com", "ourworldindata.org"],
}
INDEX_NAMES = {"freedom_house": "Freedom House Freedom in the World", "vdem": "V-Dem Regimes of the World",
               "eiu": "EIU Democracy Index"}
_LABEL_RX = {
    "free": r"(?<!partly )(?<!not )\bfree\b",
    "partly_free": r"\bpartly free\b",
    "not_free": r"\bnot free\b",
    "liberal_democracy": r"\bliberal democrac",
    "electoral_democracy": r"\belectoral democrac",
    "electoral_autocracy": r"\belectoral autocrac",
    "closed_autocracy": r"\bclosed autocrac",
    "full_democracy": r"\bfull democrac",
    "flawed_democracy": r"\bflawed democrac",
    "hybrid_regime": r"\bhybrid regime",
    "authoritarian": r"\bauthoritarian",
}


class ExtractionError(RuntimeError):
    pass


def _validate(model, run: dict):
    structured = (run.get("output") or {}).get("structured")
    if structured is None:
        raise ExtractionError(f"agent run {run.get('id')} returned no structured output")
    try:
        return model.model_validate(structured)
    except ValidationError as exc:
        raise ExtractionError(f"agent run {run.get('id')} failed schema validation: {exc}") from exc


def label_near_country(text: str, label: str, country_names: list[str], window: int = 400) -> bool:
    """The classification label must appear within `window` chars of the country name, so a
    multi-country report does not 'verify' a label that belongs to another country."""
    t = normalize_text(text)
    rx = re.compile(_LABEL_RX[label])
    for cname in country_names:
        c = normalize_text(cname)
        if not c:
            continue
        for m in re.finditer(rf"\b{re.escape(c)}\b", t):
            seg = t[max(0, m.start() - window): m.end() + window]
            if rx.search(seg):
                return True
    return False


# =====================================================================================
# Democracy
# =====================================================================================
@dataclass
class AssessmentResult:
    index: str
    label: str | None
    raw: str
    grey_zone: bool
    score: float | None
    score_scale: str | None
    year: int | None
    publication: str | None
    claimed_url: str | None
    quote: str | None
    verified: bool
    supporting: list[SupportingSource] = field(default_factory=list)
    checked_urls: list[str] = field(default_factory=list)
    agent_citations: list[str] = field(default_factory=list)


def research_democracy(gw: ExaGateway, country: CountryRecord, ttl_hours: float | None = None) -> tuple[list[AssessmentResult], dict]:
    names = _country_names(country)
    query = (
        f"Find the most recent published democracy classifications for {country.name}:\n"
        "1. Freedom House 'Freedom in the World': status (Free / Partly Free / Not Free) and global score out of 100, "
        "from the freedomhouse.org country report page.\n"
        "2. V-Dem 'Regimes of the World' classification (Liberal Democracy / Electoral Democracy / Electoral Autocracy / "
        "Closed Autocracy) from the latest V-Dem Democracy Report or v-dem.net; note if it is in the grey zone "
        "(upper/lower bound).\n"
        "3. EIU Democracy Index category (Full democracy / Flawed democracy / Hybrid regime / Authoritarian) and score.\n"
        "Report each index separately with its edition year, the exact URL that states the classification, and a short "
        "verbatim quote. Omit an index rather than guess if its classification cannot be found."
    )
    system = ("Use the index publishers' own websites as primary evidence. Never infer a classification from scores "
              "or from other indices. Indices often disagree; report each as published.")
    run = gw.agent_run(query, purpose=f"democracy:{country.iso2}", output_schema=agent_schema(DemocracyX),
                       system_prompt=system, ttl_hours=ttl_hours)
    data = _validate(DemocracyX, run)
    results: list[AssessmentResult] = []
    for i, a in enumerate(data.assessments):
        label, grey = normalize_classification(a.index, a.classification_raw)
        # V-Dem marks grey-zone cases with +/- after the regime code (e.g. "EA+", "ED-").
        if a.index == "vdem" and re.search(r"\b(LD|ED|EA|CA)\s?[+\-−](?!\w)", " ".join(filter(None, [a.classification_raw, a.evidence_quote]))):
            grey = True
        cited = list(dict.fromkeys([u for u in [a.evidence_url] if u] + urls_for_item(run, "assessments", i)))
        res = AssessmentResult(index=a.index, label=label, raw=a.classification_raw, grey_zone=grey, score=a.score,
                               score_scale=a.score_scale, year=a.assessment_year, publication=a.publication,
                               claimed_url=a.evidence_url, quote=a.evidence_quote, verified=False,
                               agent_citations=cited)
        if label:
            _verify_assessment(gw, country, res, names, cited)
        results.append(res)
    # Indices the agent did not return at all: try a direct, domain-restricted search so the
    # gate sees an honest "not found" rather than a silent gap.
    for idx in INDEX_DOMAINS:
        if not any(r.index == idx for r in results):
            res = _search_assessment(gw, country, idx, names)
            if res:
                results.append(res)
    return results, {"agent_run_id": run.get("id"), "agent_cost": (run.get("cost_dollars") or {}).get("total")}


def _verify_assessment(gw: ExaGateway, country: CountryRecord, res: AssessmentResult, names: list[str],
                       cited: list[str]) -> None:
    on_domain = [u for u in cited if any(registrable_domain(u).endswith(d) for d in INDEX_DOMAINS[res.index])]
    pages = gw.contents(on_domain or cited[:3], purpose=f"democracy-verify:{country.iso2}:{res.index}")
    res.checked_urls = list(pages)
    for u, p in pages.items():
        if p.get("text") and label_near_country(p["text"], res.label, names):
            res.supporting.append(SupportingSource(url=p["url"] or u, title=p.get("title"),
                                                   domain=registrable_domain(u), tier=authority_tier(u),
                                                   via="agent_citation", published_date=p.get("published_date")))
    if not res.supporting:
        # Fall back to searching the publisher's domain directly.
        hits = gw.search(f"{country.name} {INDEX_NAMES[res.index]} {res.year or ''} {res.raw}".strip(),
                         purpose=f"democracy-search:{country.iso2}:{res.index}", num_results=5,
                         include_domains=INDEX_DOMAINS[res.index], text_chars=20000)
        for h in hits:
            if h.get("text") and label_near_country(h["text"], res.label, names):
                res.supporting.append(SupportingSource(url=h["url"], title=h.get("title"),
                                                       domain=registrable_domain(h["url"]),
                                                       tier=authority_tier(h["url"]), via="index_search",
                                                       published_date=h.get("published_date")))
                res.checked_urls.append(h["url"])
    res.verified = bool(res.supporting)


def _search_assessment(gw: ExaGateway, country: CountryRecord, idx: str, names: list[str]) -> AssessmentResult | None:
    hits = gw.search(f"{country.name} {INDEX_NAMES[idx]} latest classification", purpose=f"democracy-search:{country.iso2}:{idx}",
                     num_results=5, include_domains=INDEX_DOMAINS[idx], text_chars=20000)
    for h in hits:
        text = h.get("text") or ""
        for label, rx in _LABEL_RX.items():
            if label not in {lab for _, lab in _LABELS[idx]}:
                continue
            if label_near_country(text, label, names):
                yr = re.search(r"\b(20\d\d)\b", (h.get("title") or "") + " " + h["url"])
                return AssessmentResult(index=idx, label=label, raw=label.replace("_", " "), grey_zone=False,
                                        score=None, score_scale=None, year=int(yr.group(1)) if yr else None,
                                        publication=h.get("title"), claimed_url=h["url"], quote=None, verified=True,
                                        supporting=[SupportingSource(url=h["url"], title=h.get("title"),
                                                                     domain=registrable_domain(h["url"]),
                                                                     tier=authority_tier(h["url"]),
                                                                     via="index_search")],
                                        checked_urls=[h["url"]])
    return None


# =====================================================================================
# Contact legality
# =====================================================================================
@dataclass
class ContactResult:
    extraction: ContactX
    evidence_ok: dict[int, bool]
    fetched: dict[str, dict]
    run_id: str | None
    citations: list[str]


def research_contact_rules(gw: ExaGateway, country: CountryRecord, ttl_hours: float | None = None) -> ContactResult:
    query = (
        f"Research the law in {country.name} on communicating with public officials about political issues. For each "
        "meaningful combination below determine whether it is allowed, restricted (allowed with conditions), prohibited, "
        "or unknown, citing the statute/article and an official URL:\n"
        "- purposes: issue_communication (contacting an elected official about a policy issue), "
        "fact_verification_request (asking an official or their office to confirm or deny a factual claim), petition, "
        "lobbying, campaign_communication\n"
        "- channels: email, postal_mail, web_form, phone, social_media\n"
        "- senders: citizen, foreign_national, organization (domestic NGO/company), foreign_organization, automated_system\n"
        "- recipients: elected_official, candidate (consider campaign periods), public_servant\n"
        "Cover the constitutional right of petition, data-protection/privacy law (use of officials' contact data, bulk "
        "messaging), election law (campaign/blackout periods, restrictions on foreigners in political affairs), "
        "lobbying regulation, and telecommunications/anti-spam rules. Include at least the organization and "
        "foreign_organization senders for issue_communication and fact_verification_request to elected officials."
    )
    system = ("This is a compliance research summary, not legal advice. Prefer primary legal texts (official gazette, "
              "legislature law library, constitution). Use 'unknown' whenever sources do not settle the question.")
    run = gw.agent_run(query, purpose=f"contact:{country.iso2}", output_schema=agent_schema(ContactX),
                       system_prompt=system, ttl_hours=ttl_hours)
    data = _validate(ContactX, run)
    urls = [d.evidence_url for d in data.dimensions if d.evidence_url] + [l.url for l in data.laws if l.url]
    fetched = gw.contents(urls, purpose=f"contact-verify:{country.iso2}", text_chars=15000)
    ok = {i: bool(d.evidence_url and canonical_url(d.evidence_url) in fetched) for i, d in enumerate(data.dimensions)}
    cites = [u for us in grounding_urls(run).values() for u in us]
    return ContactResult(data, ok, fetched, run.get("id"), list(dict.fromkeys(cites)))


# =====================================================================================
# Officeholder verification (shared)
# =====================================================================================
def verify_holder(gw: ExaGateway, settings: Settings, country: CountryRecord, claim: OfficeholderX,
                  office_titles: list[str | None], extra_keywords: list[str], context_terms: list[str] | None,
                  cited_urls: list[str], purpose: str, search_only_if_unofficial: bool = False) -> Verification:
    """`search_only_if_unofficial`: for bulk lists (legislators) skip the corroboration search when an
    official/primary page already supports the claim, to keep cost proportional to the gaps."""
    keywords = office_keywords(*office_titles, extra=extra_keywords)
    urls = list(dict.fromkeys(claim.source_urls + cited_urls))[:8]
    pages = gw.contents(urls, purpose=f"verify:{purpose}") if urls else {}
    supporting = evaluate_pages(claim.full_name, keywords, pages, via="agent_citation", context_terms=context_terms)
    reasons = []
    need_search = len({s.domain for s in supporting}) < settings.min_independent_domains
    if search_only_if_unofficial and any(s.tier == 1 for s in supporting):
        need_search = False
    if need_search and settings.corroboration_search:
        title = next((t for t in office_titles if t), "")
        q = f"{claim.full_name} {title} {' '.join(context_terms or [])} {country.name}".strip()
        hits = gw.search(q, purpose=f"corroborate:{purpose}", num_results=6, text_chars=8000,
                         exclude_domains=list({registrable_domain(s.url) for s in supporting}) or None)
        extra = evaluate_pages(claim.full_name, keywords, {canonical_url(h["url"]): h for h in hits if h.get("url")},
                               via="corroboration_search", context_terms=context_terms)
        supporting = merge_supporting(supporting, extra)
        reasons.append(f"corroboration search returned {len(hits)} results, {len(extra)} supporting")
    status = classify(supporting, settings.min_independent_domains)
    if status == EvidenceStatus.unsupported:
        reasons.append(f"none of {len(pages)} fetched cited pages mention '{claim.full_name}' with {keywords}")
    return Verification(status=status, supporting=supporting, checked_urls=list(pages), reasons=reasons)


# =====================================================================================
# Government structure
# =====================================================================================
def research_national(gw: ExaGateway, country: CountryRecord, ttl_hours: float | None = None) -> tuple[NationalStructureX, dict]:
    today = date.today().isoformat()
    query = (
        f"Describe the national (federal) government structure of {country.name} as of {today}. For each body in the "
        "executive, legislative and judicial branches, plus the national electoral authority, list its offices with: "
        "English and local-language title, how the OFFICE is filled (direct_election, indirect_election, appointment, "
        "mixed, hereditary, ex_officio), number of seats, term length, and the CURRENT holder(s) for single-seat or "
        "leadership offices only (head of state/government, presiding officer of each legislative chamber, president of "
        "the highest court, head of the electoral authority) with party and term start/end. For multi-member bodies give "
        "seat counts and selection method but do not list every member. Note recent reforms that changed how offices are "
        "filled (for example judicial elections). Also list the subnational tiers of government (first-level divisions, "
        "and municipal/local government) with counts. Give source URLs for every current officeholder."
    )
    system = ("Prefer official government, legislature, court and electoral-authority websites. Use null for anything "
              "you cannot confirm; never guess names or dates.")
    run = gw.agent_run(query, purpose=f"national:{country.iso2}", output_schema=agent_schema(NationalStructureX),
                       system_prompt=system, ttl_hours=ttl_hours)
    return _validate(NationalStructureX, run), run


def research_divisions(gw: ExaGateway, country: CountryRecord, ttl_hours: float | None = None) -> tuple[DivisionsX, dict]:
    subs = subdivisions(country.iso2)
    today = date.today().isoformat()
    names = "; ".join(s["name"] for s in subs)
    query = (
        f"List ALL {len(subs)} first-level administrative divisions of {country.name} ({names}). For each give: the "
        "executive office title in English and the local language, how that office is filled, its term length, the "
        f"CURRENT officeholder as of {today} (full name, party name and acronym, coalition, term start and end, whether "
        "acting/interim) with source URLs, and the division's legislature (English and local name, number of seats, how "
        "filled). Explain in special_status_note any division whose governance differs from the others (for example a "
        "capital district)."
    )
    system = ("Prefer each division's official government website, its legislature, the electoral authority and "
              "reputable national press. Return one entry per division; use null for anything unconfirmed.")
    run = gw.agent_run(query, purpose=f"divisions:{country.iso2}", output_schema=agent_schema(DivisionsX),
                       system_prompt=system, ttl_hours=ttl_hours)
    return _validate(DivisionsX, run), run


def research_local(gw: ExaGateway, country: CountryRecord, top_n: int = 10, ttl_hours: float | None = None) -> tuple[LocalStructureX, dict]:
    today = date.today().isoformat()
    query = (
        f"Describe municipal/local government in {country.name}: the generic name of local units and how many exist, "
        "the governing council and its offices (for example municipal president/mayor, trustees, councillors) with how "
        f"each office is filled and its term length. Then list the current heads of the {top_n} most populous "
        f"municipalities as of {today}: municipality name, the first-level division it belongs to, head title in English "
        "and local language, how the head is selected, and the current officeholder (full name, party, term start/end) "
        "with source URLs. Exclude sub-units of the national capital if the capital is governed differently."
    )
    system = "Prefer official municipal websites, state electoral institutes and reputable press. Never guess names."
    run = gw.agent_run(query, purpose=f"local:{country.iso2}", output_schema=agent_schema(LocalStructureX),
                       system_prompt=system, ttl_hours=ttl_hours)
    return _validate(LocalStructureX, run), run


def research_special(gw: ExaGateway, country: CountryRecord, ttl_hours: float | None = None) -> tuple[SpecialJurisdictionsX, dict]:
    today = date.today().isoformat()
    query = (
        f"Identify jurisdictions in {country.name} whose local government differs from the standard first-level "
        "division / municipality model — in particular the national capital. For each: explain how its governance "
        "differs, name its sub-units (e.g. boroughs) and their councils, and list EVERY sub-unit with the head's title "
        f"(English and local), how the head is selected, and the current officeholder as of {today} (full name, party, "
        "term start/end) with source URLs."
    )
    system = "Prefer the jurisdiction's official websites and its electoral institute. Use null when unconfirmed."
    run = gw.agent_run(query, purpose=f"special:{country.iso2}", output_schema=agent_schema(SpecialJurisdictionsX),
                       system_prompt=system, ttl_hours=ttl_hours)
    return _validate(SpecialJurisdictionsX, run), run


def _country_names(country: CountryRecord) -> list[str]:
    names = {country.name}
    try:
        import pycountry
        c = pycountry.countries.get(alpha_2=country.iso2)
        for attr in ("name", "official_name", "common_name"):
            if getattr(c, attr, None):
                names.add(getattr(c, attr))
    except Exception:
        pass
    # A few local-language names for the MVP and common cases.
    local = {"MX": ["México"], "BR": ["Brasil"], "DE": ["Deutschland"], "ES": ["España"], "FR": ["France"]}
    names.update(local.get(country.iso2, []))
    return sorted(names, key=len)


# =====================================================================================
# Legislatures: individual members
# =====================================================================================
def research_assembly_structure(gw: ExaGateway, country: CountryRecord, assembly: str, division: str,
                                ttl_hours: float | None = None):
    from .schemas import AssemblyStructureX
    query = (
        f"For the {assembly} ({division}, {country.name}) give its current composition: total seats, number of general "
        "(constituency) seats and how they are filled, the constituency code prefix and the first and last constituency "
        "numbers (e.g. PP-1 to PP-297), the number of seats reserved for women, for minorities/non-Muslims and any "
        "other reserved category and how reserved seats are filled, the date of the last general election, and the URL "
        "of the official page listing current members."
    )
    run = gw.agent_run(query, purpose=f"assembly:{country.iso2}:{division}",
                       output_schema=agent_schema(AssemblyStructureX),
                       system_prompt="Prefer the assembly's official website and the national election commission.",
                       ttl_hours=ttl_hours, max_cost_usd=1.0)
    return _validate(AssemblyStructureX, run), run


def research_legislators(gw: ExaGateway, country: CountryRecord, assembly: str, division: str, scope: str,
                         ttl_hours: float | None = None):
    """`scope` describes the slice, e.g. 'general seats for constituencies PP-1 to PP-50'."""
    from .schemas import LegislatorsX
    today = date.today().isoformat()
    query = (
        f"List the CURRENT members of the {assembly} ({division}, {country.name}) holding the {scope}, as of {today}. "
        "For each member give full name, constituency code and name (for constituency seats), seat type "
        "(general, women_reserved, minority_reserved, other_reserved), party name and acronym, term start, whether the "
        "seat was won in a by-election, and source URLs that list this person as a member for this seat. Account for "
        "by-elections, deaths, resignations and disqualifications since the general election; omit vacant seats."
    )
    system = ("Use the assembly's official member directory, the election commission's notifications of returned "
              "candidates and reserved-seat allocations, and reputable national press. Never guess a name; omit a seat "
              "rather than invent its member.")
    run = gw.agent_run(query, purpose=f"legislators:{country.iso2}:{division}:{scope}"[:200],
                       output_schema=agent_schema(LegislatorsX), system_prompt=system, ttl_hours=ttl_hours)
    return _validate(LegislatorsX, run), run
