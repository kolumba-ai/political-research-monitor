"""Country enrichment pipeline.

Seed registry -> democracy research -> democracy gate -> contact-rule research -> contact gate ->
[production: stop unless eligible | research_only: continue, tagged] -> national structure ->
first-level divisions -> municipal -> special jurisdictions -> verified graph upserts.

Each stage is independent and idempotent so it can later run as its own job (e.g. one Modal
function per country/stage). Stages are skipped when fresh unless `force=True` (incremental refresh).
"""
from __future__ import annotations

import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

from . import ids
from .config import Settings
from .eligibility import (AssessmentView, ContactDim, ContactPolicy, DemocracyPolicy, Gate, aggregate_contact_status,
                          evaluate_contact, evaluate_democracy, overall_eligibility)
from .evidence import PERSISTABLE, EvidenceStatus, authority_tier, SupportingSource, classify, mentions_any, office_keywords
from .exa_gateway import BudgetExceeded, ExaGateway, urls_for_item, urls_for_path
from .graph.store import GraphStore
from .graph.writer import GraphWriter
from .ids import registrable_domain, utcnow
from .logging_setup import get_logger
from .registry import CountryRecord, get_country, load_registry, match_subdivision, subdivisions
from .research import (INDEX_NAMES, ExtractionError, research_contact_rules, research_democracy, research_divisions,
                       research_local, research_national, research_special, verify_holder)
from .schemas import OfficeX, office_is_elected
from .structure import local_container, tier_name
from .state import PipelineState

log = get_logger("pipeline")

ELIGIBILITY_STAGES = ["democracy", "contact"]
STRUCTURE_STAGES = ["national", "divisions", "local", "special"]
ALL_STAGES = ELIGIBILITY_STAGES + STRUCTURE_STAGES
# Opt-in (run with --stages legislators): individual members of first-level legislatures. Expensive —
# one Agent run per ~50 constituencies — so it is not part of the default build.
OPTIONAL_STAGES = ["legislators", "directories"]
CHUNK = 50

DISCLAIMER = ("Research and compliance assessment only — not legal advice or authorisation. "
              "This system never initiates political outreach.")


class Pipeline:
    def __init__(self, settings: Settings, gateway: ExaGateway, store: GraphStore, state: PipelineState):
        self.s = settings
        self.gw = gateway
        self.store = store
        self.state = state

    # ================================================================== entry points
    def seed_world(self) -> int:
        """Earth -> continents -> every ISO 3166-1 country (no Exa calls)."""
        w = GraphWriter(self.store, "seed", "seed")
        w.node(ids.earth_id(), "Earth", name="Earth", description="Conceptual root of the political knowledge graph")
        n = 0
        for rec in load_registry().values():
            cid = ids.continent_id(rec.continent)
            w.node(cid, "Continent", name=rec.continent)
            w.edge(ids.earth_id(), "CONTAINS", cid)
            self._seed_country(w, rec)
            n += 1
        return n

    def _seed_country(self, w: GraphWriter, rec: CountryRecord) -> str:
        cid = ids.country_id(rec.iso2)
        w.node(cid, "Country", name=rec.name, iso2=rec.iso2, iso3=rec.iso3, m49=rec.m49, continent=rec.continent,
               un_region=rec.region, un_sub_region=rec.sub_region, un_intermediate_region=rec.intermediate_region or None,
               registry_source="ISO 3166-1 + UN M49 (lukes/ISO-3166-Countries-with-Regional-Codes)")
        if not (self.store.get_node(cid) or {}).get("research_status"):
            w.node(cid, "Country", research_status="not_researched")
        w.edge(ids.continent_id(rec.continent), "CONTAINS", cid)
        return cid

    def run(self, iso2: str, mode: str = "production", stages: list[str] | None = None, force: bool = False,
            no_cache: bool = False, divisions: list[str] | None = None) -> dict:
        if mode not in ("production", "research_only"):
            raise ValueError("mode must be production|research_only")
        country = get_country(iso2)
        run_id = (f"run-{country.iso2}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                  f"{secrets.token_hex(3)}")
        stages = stages or ALL_STAGES
        self.state.start_run(run_id, country.iso2, mode)
        w = GraphWriter(self.store, mode, run_id)
        self.seed_world()
        summary: dict = {"run_id": run_id, "country": country.iso2, "mode": mode, "stages": {}}
        log.info("pipeline.start", run_id=run_id, country=country.iso2, mode=mode, stages=stages, force=force,
                 no_cache=no_cache)
        self._no_cache = no_cache
        self._divisions = {d.upper() for d in divisions} if divisions else None
        status = "ok"
        try:
            for st in ELIGIBILITY_STAGES:
                if st in stages:
                    summary["stages"][st] = self._stage(st, country, w, force)
            gates = self.evaluate_gates(country)
            summary["gates"] = gates
            if not gates["eligible"] and mode == "production":
                summary["halted"] = ("country did not pass the eligibility gates; production enrichment stopped. "
                                     "Re-run with --research-only to explore without passing.")
                log.warning("pipeline.halted_by_gate", country=country.iso2, gates=gates)
            else:
                for st in STRUCTURE_STAGES + OPTIONAL_STAGES:
                    if st in stages:
                        summary["stages"][st] = self._stage(st, country, w, force)
                self.store.upsert_node(ids.country_id(country.iso2), "Country", {
                    "research_status": "enriched" if gates["eligible"] else "research_only_enriched",
                    "last_verified": utcnow()})
        except BudgetExceeded as exc:
            status = "budget_exceeded"
            summary["error"] = str(exc)
            log.error("pipeline.budget_exceeded", error=str(exc))
        summary["coverage"] = self.coverage(country.iso2)
        summary["usage"] = self.gw.usage_report()["this_run"]
        summary["unresolved_claims"] = len(self.state.claims(country.iso2, run_id))
        self.state.finish_run(run_id, status, summary)
        log.info("pipeline.finish", run_id=run_id, status=status, usage=summary["usage"])
        return summary

    # ================================================================== stage runner
    def _stage(self, stage: str, country: CountryRecord, w: GraphWriter, force: bool) -> dict:
        ttl = {"democracy": self.s.ttl_democracy_days, "contact": self.s.ttl_contact_days,
               "national": self.s.ttl_structure_days, "divisions": self.s.ttl_officeholders_days,
               "local": self.s.ttl_officeholders_days, "special": self.s.ttl_officeholders_days,
               "legislators": self.s.ttl_officeholders_days, "directories": self.s.ttl_officeholders_days}[stage]
        stage_key = stage if stage != "legislators" or not getattr(self, "_divisions", None) \
            else f"legislators:{','.join(sorted(self._divisions))}"
        if not force and self.state.stage_fresh(country.iso2, stage_key, ttl):
            log.info("stage.skip_fresh", stage=stage, country=country.iso2, ttl_days=ttl)
            return {"skipped": "fresh"}
        fn: Callable = getattr(self, f"_stage_{stage}")
        log.info("stage.start", stage=stage, country=country.iso2)
        try:
            result = fn(country, w, 0 if getattr(self, "_no_cache", False) else None)
        except BudgetExceeded:
            self.state.mark_stage(country.iso2, stage_key, "budget_exceeded", {})
            raise
        except (ExtractionError, RuntimeError, ValueError) as exc:
            log.error("stage.failed", stage=stage, error=str(exc)[:500])
            self.state.mark_stage(country.iso2, stage_key, "failed", {"error": str(exc)[:500]})
            return {"failed": str(exc)[:500]}
        self.state.mark_stage(country.iso2, stage_key, "ok", result)
        log.info("stage.done", stage=stage, **{k: v for k, v in result.items() if isinstance(v, (int, str))})
        return result

    # ================================================================== eligibility stages
    def _stage_democracy(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        results, meta = research_democracy(self.gw, country, ttl_hours=ttl_hours)
        cid = ids.country_id(country.iso2)
        out = {"assessments": 0, "verified": 0, "agent_run_id": meta.get("agent_run_id")}
        for r in results:
            aid = ids.assessment_id(country.iso2, r.index, r.year)
            status = "verified" if r.verified else "unverified"
            w.node(aid, "DemocracyAssessment", name=f"{INDEX_NAMES[r.index]} {r.year or ''}: {r.raw}".strip(),
                   index=r.index, classification=r.label, classification_raw=r.raw, grey_zone=r.grey_zone,
                   score=r.score, score_scale=r.score_scale, assessment_year=r.year, publication=r.publication,
                   evidence_url=r.claimed_url, evidence_quote=r.quote, verification=status,
                   checked_urls=r.checked_urls, agent_citations=r.agent_citations, agent_run_id=meta.get("agent_run_id"),
                   **w.provenance("verified" if r.verified else "unsupported", 0.9 if r.verified else 0.0))
            w.edge(cid, "HAS_ASSESSMENT", aid)
            w.support(aid, r.supporting, claim=f"{r.index} classifies {country.name} as {r.label}")
            out["assessments"] += 1
            out["verified"] += int(r.verified)
            if not r.verified:
                self.state.record_claim(country.iso2, "democracy", r.index, asdict(r) | {"supporting": []},
                                        "unsupported", "classification label not found near country name on any "
                                        "fetched evidence page", r.checked_urls or r.agent_citations, w.run_ref)
        return out

    def _stage_contact(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        res = research_contact_rules(self.gw, country, ttl_hours=ttl_hours)
        policy = ContactPolicy.from_settings(self.s)
        dims = [ContactDim(d.purpose, d.channel, d.sender, d.recipient, d.status, res.evidence_ok.get(i, False))
                for i, d in enumerate(res.extraction.dimensions)]
        agg, reasons = aggregate_contact_status(dims, policy)
        review = self.state.latest_review(country.iso2, "contact")
        rid = ids.contact_rule_id(country.iso2)
        w.node(rid, "ContactRule", name=f"Political contact & verification rules — {country.name}",
               status=agg, status_reasons=reasons,
               review_status={"approve": "approved", "reject": "rejected"}.get((review or {}).get("decision"),
                                                                                "pending_human_review"),
               review=review,
               dimensions=[d.model_dump() | {"evidence_fetched": res.evidence_ok.get(i, False)}
                           for i, d in enumerate(res.extraction.dimensions)],
               laws=[l.model_dump() for l in res.extraction.laws], notes=res.extraction.notes,
               relevant_policy={"purposes": sorted(policy.relevant_purposes), "senders": sorted(policy.relevant_senders)},
               agent_run_id=res.run_id, disclaimer=DISCLAIMER,
               **w.provenance("evidence_fetched" if any(res.evidence_ok.values()) else "unsupported"))
        w.edge(ids.country_id(country.iso2), "HAS_CONTACT_RULE", rid)
        for url, page in res.fetched.items():
            w.support(rid, [SupportingSource(url=page.get("url") or url, title=page.get("title"),
                                             domain=registrable_domain(url), tier=authority_tier(url),
                                             via="agent_citation")], claim="legal basis cited for contact rule")
        for i, d in enumerate(res.extraction.dimensions):
            if not res.evidence_ok.get(i):
                self.state.record_claim(country.iso2, "contact", f"{d.purpose}/{d.channel}/{d.sender}",
                                        d.model_dump(), "unsupported", "evidence URL missing or not fetchable",
                                        [d.evidence_url] if d.evidence_url else [], w.run_ref)
        return {"dimensions": len(dims), "aggregate_status": agg, "laws": len(res.extraction.laws),
                "agent_run_id": res.run_id}

    def evaluate_gates(self, country: CountryRecord) -> dict:
        cid = ids.country_id(country.iso2)
        views = [AssessmentView(index=n["index"], label=n.get("classification"), year=n.get("assessment_year"),
                                verified=n.get("verification") == "verified", grey_zone=bool(n.get("grey_zone")))
                 for _, n in self.store.edges(cid, "HAS_ASSESSMENT", "out")]
        d_review = (self.state.latest_review(country.iso2, "democracy") or {}).get("decision")
        dem = evaluate_democracy(views, DemocracyPolicy.from_settings(self.s), review_decision=d_review)
        rule = self.store.get_node(ids.contact_rule_id(country.iso2))
        c_review = self.state.latest_review(country.iso2, "contact")
        review_status = {"approve": "approved", "reject": "rejected"}.get((c_review or {}).get("decision"))
        if rule:
            con = evaluate_contact(rule.get("status", "unknown"), ContactPolicy.from_settings(self.s), review_status,
                                   rule.get("status_reasons"))
            self.store.upsert_node(rule["id"], "ContactRule", {"review_status": review_status or "pending_human_review",
                                                               "review": c_review, "gate": con.to_dict()})
        else:
            con = evaluate_contact("unknown", ContactPolicy.from_settings(self.s), None, ["contact rules not researched"])
        eligible = overall_eligibility(dem, con)
        out = {"democracy": dem.to_dict(), "contact": con.to_dict(), "eligible": eligible, "evaluated_at": utcnow()}
        self.store.upsert_node(cid, "Country", {"democracy_gate": out["democracy"], "contact_gate": out["contact"],
                                                "eligible": eligible, "eligibility_evaluated_at": out["evaluated_at"]})
        for _, n in self.store.edges(cid, "HAS_ASSESSMENT", "out"):
            self.store.upsert_node(n["id"], "DemocracyAssessment", {"gate_status": dem.status.value})
        return out

    # ================================================================== structure stages
    def _level(self, w: GraphWriter, country: CountryRecord, tier: str, name: str, **props) -> str:
        lid = ids.level_id(country.iso2, tier)
        w.node(lid, "GovernmentLevel", name=name, tier=tier, country=country.iso2, **props)
        w.edge(ids.country_id(country.iso2), "HAS_GOVERNMENT_LEVEL", lid)
        return lid

    def _office_with_holders(self, w: GraphWriter, country: CountryRecord, body_id: str, office: OfficeX,
                             cited: list[str], context_terms: list[str] | None, stage: str) -> dict:
        kw = office_keywords(office.title_en, office.title_local, extra=office.keywords)
        office_status, office_sources = self._verify_office(office.source_urls or cited[:4], kw, stage)
        single = office.seats == 1 or (office.seats is None and len(office.current_holders) == 1)
        oid = w.office(body_id, title_en=office.title_en, title_local=office.title_local,
                       selection_method=office.selection_method, is_elected=office_is_elected(office.selection_method),
                       selection_details=office.selection_details, seats=office.seats,
                       term_length_years=office.term_length_years, keywords=kw, single_seat=single,
                       structure_evidence=office_status, last_verified=utcnow(), pipeline_mode=w.mode,
                       pipeline_run=w.run_ref)
        w.support(oid, office_sources, claim=f"office exists: {office.title_en}")
        stats = {"offices": 1, "holders_persisted": 0, "holders_unresolved": 0}
        for h in office.current_holders:
            stats_h = self._holder(w, country, oid, h, [office.title_en, office.title_local], office.keywords,
                                   context_terms, cited + office.source_urls, single, stage)
            for k in ("holders_persisted", "holders_unresolved"):
                stats[k] += stats_h[k]
        return stats

    def _verify_office(self, urls: list[str], keywords: list[str], stage: str) -> tuple[str, list[SupportingSource]]:
        if not urls:
            return "uncited", []
        pages = self.gw.contents(urls[:4], purpose=f"office-verify:{stage}", text_chars=20000)
        sup = [SupportingSource(url=p.get("url") or u, title=p.get("title"), domain=registrable_domain(u),
                                tier=authority_tier(u), via="agent_citation")
               for u, p in pages.items() if mentions_any(keywords, p.get("text") or "")]
        return classify(sup, self.s.min_independent_domains).value, sup

    def _holder(self, w: GraphWriter, country: CountryRecord, office_id: str, claim, titles, extra_kw, context,
                cited: list[str], single: bool, stage: str) -> dict:
        ver = verify_holder(self.gw, self.s, country, claim, titles, extra_kw or [], context, cited,
                            purpose=f"{stage}:{office_id}")
        if ver.status in PERSISTABLE:
            w.holding(country.iso2, office_id, claim, ver, single_seat=single)
            if ver.status != EvidenceStatus.verified:
                self.state.record_claim(country.iso2, stage, f"{claim.full_name} @ {office_id}", claim.model_dump(),
                                        ver.status.value, "persisted with weaker corroboration: " + "; ".join(ver.reasons),
                                        [s.url for s in ver.supporting], w.run_ref)
            return {"holders_persisted": 1, "holders_unresolved": 0}
        self.state.record_claim(country.iso2, stage, f"{claim.full_name} @ {office_id}", claim.model_dump(),
                                ver.status.value, "; ".join(ver.reasons) or "not corroborated", ver.checked_urls,
                                w.run_ref)
        return {"holders_persisted": 0, "holders_unresolved": 1}

    def _stage_national(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        data, run = research_national(self.gw, country, ttl_hours=ttl_hours)
        self.store.upsert_node(ids.country_id(country.iso2), "Country", {
            "system_of_government": data.system_of_government, "constitution_name": data.constitution_name,
            "constitution_url": data.constitution_url})
        lid = self._level(w, country, "national", tier_name(country.iso2, "national"), agent_run_id=run.get("id"))
        stats = {"bodies": 0, "offices": 0, "holders_persisted": 0, "holders_unresolved": 0, "agent_run_id": run.get("id")}
        for bi, body in enumerate(data.bodies):
            bid = ids.body_id(lid, body.name_en)
            w.node(bid, "GovernmentBody", name=body.name_en, name_local=body.name_local, branch=body.branch,
                   description=body.description, last_verified=utcnow(), pipeline_mode=w.mode)
            w.edge(lid, "HAS_BODY", bid)
            stats["bodies"] += 1
            for oi, office in enumerate(body.offices):
                cited = urls_for_path(run, [("bodies", bi), ("offices", oi)]) + body.source_urls
                st = self._office_with_holders(w, country, bid, office, cited, None, "national")
                for k in ("offices", "holders_persisted", "holders_unresolved"):
                    stats[k] += st[k]
        local_notes = []
        for lvl in data.subnational_levels:
            t = lvl.tier.lower()
            if "state" in t or "province" in t or "division" in t:
                self._level(w, country, "state", tier_name(country.iso2, "state"), name_local=lvl.name_local,
                            researched_name=lvl.name_en, unit_count=lvl.count, description=lvl.description)
            else:
                # Local government is nested under each division (AGENTS.md 2.1), never a country-wide tier.
                local_notes.append({"name": lvl.name_en, "name_local": lvl.name_local, "count": lvl.count,
                                    "description": lvl.description})
        if local_notes:
            self.store.upsert_node(ids.country_id(country.iso2), "Country", {"local_government_summary": local_notes})
        return stats

    def _stage_divisions(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        data, run = research_divisions(self.gw, country, ttl_hours=ttl_hours)
        state_lid = self._level(w, country, "state", tier_name(country.iso2, "state"))
        expected = {s["code"] for s in subdivisions(country.iso2)}
        covered: set[str] = set()
        stats = {"divisions": 0, "offices": 0, "holders_persisted": 0, "holders_unresolved": 0, "unmatched": 0,
                 "agent_run_id": run.get("id")}
        for i, d in enumerate(data.divisions):
            sub = match_subdivision(country.iso2, d.name)
            if not sub:
                stats["unmatched"] += 1
                self.state.record_claim(country.iso2, "divisions", d.name, d.model_dump(), "unmatched",
                                        "division name does not match any ISO 3166-2 subdivision", [], w.run_ref)
                continue
            covered.add(sub["code"])
            dlid = ids.level_id(country.iso2, "state", sub["code"])
            w.node(dlid, "GovernmentLevel", name=sub["name"], tier="state_unit", iso_3166_2=sub["code"],
                   iso_type=sub["type"], researched_name=d.name, division_type=d.division_type,
                   special_status_note=d.special_status_note, country=country.iso2, last_verified=utcnow(),
                   pipeline_mode=w.mode)
            w.edge(state_lid, "CONTAINS", dlid)
            stats["divisions"] += 1
            cited = urls_for_item(run, "divisions", i)
            context = list({d.name, sub["name"]})
            exec_body = ids.body_id(dlid, "executive")
            w.node(exec_body, "GovernmentBody", name=f"Executive of {sub['name']}", branch="executive",
                   pipeline_mode=w.mode, last_verified=utcnow())
            w.edge(dlid, "HAS_BODY", exec_body)
            office = OfficeX(title_en=f"{d.executive_title_en} of {sub['name']}", title_local=d.executive_title_local,
                             selection_method=d.executive_selection_method, seats=1,
                             term_length_years=d.executive_term_years,
                             keywords=[d.executive_title_en] + ([d.executive_title_local] if d.executive_title_local else []),
                             current_holders=[d.officeholder] if d.officeholder else [], source_urls=[])
            st = self._office_with_holders(w, country, exec_body, office, cited, context, "divisions")
            for k in ("offices", "holders_persisted", "holders_unresolved"):
                stats[k] += st[k]
            if d.legislature_name_en:
                leg = ids.body_id(dlid, "legislature")
                w.node(leg, "GovernmentBody", name=f"{d.legislature_name_en} ({sub['name']})",
                       name_local=d.legislature_name_local, branch="legislative", pipeline_mode=w.mode,
                       last_verified=utcnow())
                w.edge(dlid, "HAS_BODY", leg)
                lo = OfficeX(title_en=f"Member of the {d.legislature_name_en}", title_local=None,
                             selection_method=d.legislature_selection_method or "unknown", seats=d.legislature_seats,
                             keywords=["diputad", "deputy", "legislat", "congres"], source_urls=[])
                stats["offices"] += self._office_with_holders(w, country, leg, lo, cited, context, "divisions")["offices"]
        stats["iso_expected"] = len(expected)
        stats["iso_covered"] = len(covered & expected)
        stats["iso_missing"] = sorted(expected - covered)
        return stats

    def _stage_local(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        data, run = research_local(self.gw, country, ttl_hours=ttl_hours)
        # Generic local structure is recorded on the Country; local units nest under their division.
        self.store.upsert_node(ids.country_id(country.iso2), "Country", {"local_government_researched": {
            "unit_name": data.unit_name_en, "unit_name_local": data.unit_name_local, "total_units": data.total_units,
            "council_name": data.council_name_en, "council_name_local": data.council_name_local,
            "offices": [o.model_dump(include={"title_en", "title_local", "selection_method", "selection_details"})
                        for o in data.offices]}})
        stats = {"units": 0, "offices": 0, "holders_persisted": 0, "holders_unresolved": 0, "unplaced": 0,
                 "agent_run_id": run.get("id")}
        for i, u in enumerate(data.units):
            sub = match_subdivision(country.iso2, u.parent_division) if u.parent_division else None
            if not sub:
                stats["unplaced"] += 1
                self.state.record_claim(country.iso2, "local", u.name, u.model_dump(), "unplaced",
                                        "local unit's parent division not resolved; not attached (no country-wide tier)",
                                        [], w.run_ref)
                continue
            parent = local_container(w, country.iso2, sub["code"], sub["name"])
            ulid = ids.level_id(country.iso2, "municipal", sub["code"], u.name)
            w.node(ulid, "GovernmentLevel", name=f"{u.name}, {sub['name']}", tier="municipal_unit",
                   parent_division=u.parent_division, parent_iso_3166_2=sub["code"],
                   country=country.iso2, pipeline_mode=w.mode, last_verified=utcnow())
            w.edge(parent, "CONTAINS", ulid)
            body = ids.body_id(ulid, "council")
            w.node(body, "GovernmentBody", name=f"{data.council_name_en or 'Municipal government'} of {u.name}",
                   name_local=f"{data.council_name_local} de {u.name}" if data.council_name_local else None,
                   branch="executive", pipeline_mode=w.mode)
            w.edge(ulid, "HAS_BODY", body)
            office = OfficeX(title_en=f"{u.head_title_en} of {u.name}", title_local=u.head_title_local,
                             selection_method=u.head_selection_method, seats=1,
                             keywords=[u.head_title_en] + ([u.head_title_local] if u.head_title_local else []),
                             current_holders=[u.officeholder] if u.officeholder else [])
            st = self._office_with_holders(w, country, body, office, urls_for_item(run, "units", i), [u.name], "local")
            stats["units"] += 1
            for k in ("offices", "holders_persisted", "holders_unresolved"):
                stats[k] += st[k]
        return stats

    def _stage_special(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        data, run = research_special(self.gw, country, ttl_hours=ttl_hours)
        state_lid = self._level(w, country, "state", tier_name(country.iso2, "state"))
        stats = {"jurisdictions": 0, "subunits": 0, "offices": 0, "holders_persisted": 0, "holders_unresolved": 0,
                 "agent_run_id": run.get("id")}
        for ji, j in enumerate(data.jurisdictions):
            sub = match_subdivision(country.iso2, j.name)
            if not sub:
                self.state.record_claim(country.iso2, "special", j.name, {"why_distinct": j.why_distinct}, "unplaced",
                                        "special jurisdiction not matched to a first-level division; not attached",
                                        j.source_urls, w.run_ref)
                continue
            code = sub["code"]
            if sub:
                dlid = ids.level_id(country.iso2, "state", sub["code"])
                w.node(dlid, "GovernmentLevel", name=sub["name"], tier="state_unit", iso_3166_2=sub["code"],
                       special_status=True, why_distinct=j.why_distinct, country=country.iso2)
                w.edge(state_lid, "CONTAINS", dlid)
            container = ids.level_id(country.iso2, "municipal", code)
            w.node(container, "GovernmentLevel", name=tier_name(country.iso2, "special_local").format(division=sub["name"]),
                   tier="special_local", special_status=True, why_distinct=j.why_distinct,
                   subunit_name_local=j.subunit_name_local, parent_iso_3166_2=code,
                   country=country.iso2, pipeline_mode=w.mode, last_verified=utcnow())
            w.edge(ids.level_id(country.iso2, "state", code), "CONTAINS", container)
            w.support(container, j.source_urls, via="agent_citation", claim="special jurisdiction structure")
            stats["jurisdictions"] += 1
            for i, u in enumerate(j.subunits):
                ulid = ids.level_id(country.iso2, "municipal", code, u.name)
                w.node(ulid, "GovernmentLevel", name=u.name, tier="special_local_unit", country=country.iso2,
                       pipeline_mode=w.mode, last_verified=utcnow())
                w.edge(container, "CONTAINS", ulid)
                body = ids.body_id(ulid, "government")
                w.node(body, "GovernmentBody", name=f"{j.subunit_council_name_en or 'Government'} of {u.name}",
                       name_local=j.subunit_council_name_local, branch="executive", pipeline_mode=w.mode)
                w.edge(ulid, "HAS_BODY", body)
                office = OfficeX(title_en=f"{u.head_title_en} of {u.name}", title_local=u.head_title_local,
                                 selection_method=u.head_selection_method, seats=1,
                                 keywords=[u.head_title_en] + ([u.head_title_local] if u.head_title_local else []),
                                 current_holders=[u.officeholder] if u.officeholder else [])
                st = self._office_with_holders(w, country, body, office,
                                               urls_for_path(run, [("jurisdictions", ji), ("subunits", i)]) + j.source_urls, [u.name], "special")
                stats["subunits"] += 1
                for k in ("offices", "holders_persisted", "holders_unresolved"):
                    stats[k] += st[k]
        return stats

    # ================================================================== legislators (opt-in)
    def _stage_legislators(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        import re as _re
        from .research import research_assembly_structure, research_legislators
        from .schemas import SeatType
        state_lid = ids.level_id(country.iso2, "state")
        units = [n for _, n in self.store.edges(state_lid, "CONTAINS", "out") if n.get("iso_3166_2")]
        if getattr(self, "_divisions", None):
            units = [u for u in units if u["iso_3166_2"] in self._divisions]
        if not units:
            raise ValueError("no first-level divisions in the graph; run the 'divisions' stage first")
        out: dict = {"assemblies": {}, "members_persisted": 0, "members_unresolved": 0}
        for u in units:
            body_id = ids.body_id(u["id"], "legislature")
            body = self.store.get_node(body_id)
            if not body:
                out["assemblies"][u["iso_3166_2"]] = "no legislature recorded for this division"
                continue
            assembly = _re.sub(r"\s*\([^)]*\)$", "", body["name"])
            struct, srun = research_assembly_structure(self.gw, country, assembly, u["name"], ttl_hours=ttl_hours)
            w.node(body_id, "GovernmentBody", total_seats=struct.total_seats, general_seats=struct.general_seats,
                   reserved_women=struct.reserved_women, reserved_minorities=struct.reserved_minorities,
                   reserved_other=struct.reserved_other, constituency_prefix=struct.constituency_prefix,
                   constituency_range=(f"{struct.constituency_prefix}-{struct.constituency_first} to "
                                       f"{struct.constituency_prefix}-{struct.constituency_last}")
                   if struct.constituency_prefix and struct.constituency_first and struct.constituency_last else None,
                   last_general_election=struct.last_general_election,
                   official_members_url=struct.official_members_url, structure_agent_run=srun.get("id"))
            w.support(body_id, struct.source_urls + ([struct.official_members_url] if struct.official_members_url else []),
                      via="agent_citation", claim="assembly composition")

            # Offices by seat type: general seats reuse the existing "Member of …" office.
            generic = next((n for _, n in self.store.edges(body_id, "HAS_OFFICE", "out")), None)
            offices: dict[str, str] = {}
            gen_title = f"Member of the {assembly} (general seat)"
            if generic:
                w.node(generic["id"], "PoliticalOffice", name=gen_title,
                       aliases=sorted({*(generic.get("aliases") or []), generic["name"]} - {gen_title}),
                       seat_type="general", seats=struct.general_seats or generic.get("seats"),
                       selection_method=struct.general_seat_selection_method,
                       is_elected=office_is_elected(struct.general_seat_selection_method), single_seat=False)
                offices["general"] = generic["id"]
            else:
                offices["general"] = w.office(body_id, title_en=gen_title, title_local=None,
                                              selection_method=struct.general_seat_selection_method,
                                              is_elected=office_is_elected(struct.general_seat_selection_method),
                                              seats=struct.general_seats, seat_type="general", single_seat=False)
            res_method = struct.reserved_selection_method or "unknown"
            for st, n_seats, label in [("women_reserved", struct.reserved_women, "reserved seat for women"),
                                       ("minority_reserved", struct.reserved_minorities, "reserved seat for minorities"),
                                       ("other_reserved", struct.reserved_other, "other reserved seat")]:
                if n_seats:
                    offices[st] = w.office(body_id, title_en=f"Member of the {assembly} ({label})", title_local=None,
                                           selection_method=res_method, is_elected=office_is_elected(res_method),
                                           seats=n_seats, seat_type=st, single_seat=False)

            # Slices: constituency ranges for general seats, one slice for reserved seats.
            scopes = []
            if struct.constituency_prefix and struct.constituency_first and struct.constituency_last:
                for a in range(struct.constituency_first, struct.constituency_last + 1, CHUNK):
                    b = min(a + CHUNK - 1, struct.constituency_last)
                    scopes.append(f"general seats for constituencies {struct.constituency_prefix}-{a} to "
                                  f"{struct.constituency_prefix}-{b}")
            else:
                scopes += [f"general seats held by members whose surname starts with {r}"
                           for r in ("A-E", "F-L", "M-R", "S-Z")]
            if (struct.reserved_women or 0) + (struct.reserved_minorities or 0) + (struct.reserved_other or 0):
                scopes.append("seats reserved for women, for minorities/non-Muslims and any other reserved category")

            a_stats = {"seats": struct.total_seats, "persisted": 0, "unresolved": 0, "slices": len(scopes)}
            for scope in scopes:
                data, run = research_legislators(self.gw, country, assembly, u["name"], scope, ttl_hours=ttl_hours)
                for i, m in enumerate(data.members):
                    st = m.seat_type if isinstance(m.seat_type, str) else m.seat_type.value
                    oid = offices.get(st) or offices.get("other_reserved") or offices["general"]
                    context = [c for c in (m.constituency_code, m.constituency_name, u["name"]) if c]
                    ver = verify_holder(self.gw, self.s, country, m, [assembly, body.get("name_local"), "member provincial assembly"],
                                        ["mpa", "member", "assembly", "elected", "notified"], context,
                                        urls_for_item(run, "members", i) + ([struct.official_members_url]
                                                                            if struct.official_members_url else []),
                                        purpose=f"legislators:{u['iso_3166_2']}:{m.constituency_code or m.full_name}",
                                        search_only_if_unofficial=True)
                    extra = {"seat_type": st, "constituency_code": m.constituency_code,
                             "constituency_name": m.constituency_name, "by_election": m.by_election}
                    subject = f"{m.full_name} @ {m.constituency_code or st} ({assembly})"
                    if ver.status in PERSISTABLE:
                        w.holding(country.iso2, oid, m, ver, single_seat=False, extra=extra)
                        a_stats["persisted"] += 1
                        if ver.status != EvidenceStatus.verified:
                            self.state.record_claim(country.iso2, "legislators", subject, m.model_dump(),
                                                    ver.status.value, "persisted with weaker corroboration",
                                                    [s.url for s in ver.supporting], w.run_ref)
                    else:
                        a_stats["unresolved"] += 1
                        self.state.record_claim(country.iso2, "legislators", subject, m.model_dump(),
                                                ver.status.value, "; ".join(ver.reasons) or "not corroborated",
                                                ver.checked_urls, w.run_ref)
            w.node(body_id, "GovernmentBody", members_verified=a_stats["persisted"], last_verified=utcnow())
            out["assemblies"][u["iso_3166_2"]] = a_stats
            out["members_persisted"] += a_stats["persisted"]
            out["members_unresolved"] += a_stats["unresolved"]
            log.info("legislators.assembly_done", division=u["iso_3166_2"], **a_stats)
        return out

    # ================================================================== official directories (opt-in)
    def _stage_directories(self, country: CountryRecord, w: GraphWriter, ttl_hours) -> dict:
        from .directories import ADAPTERS
        adapters = ADAPTERS.get(country.iso2, [])
        if not adapters:
            raise ValueError(f"no official-directory adapters registered for {country.iso2}")
        out = {}
        for fn in adapters:
            try:
                out[fn.__name__] = fn(self.gw, self.store, w, country)
            except (RuntimeError, ValueError) as exc:
                log.error("directory.failed", adapter=fn.__name__, error=str(exc)[:300])
                out[fn.__name__] = {"failed": str(exc)[:300]}
            log.info("directory.done", adapter=fn.__name__, result=out[fn.__name__])
        return out

    # ================================================================== reporting
    def coverage(self, iso2: str) -> dict:
        cid = ids.country_id(iso2)
        counts: dict[str, int] = {}
        holders = {"verified": 0, "official_single": 0, "single_source": 0}
        offices_elected = offices_total = offices_with_holder = 0
        for nid in self.store.bfs_ids(cid):
            n = self.store.get_node(nid)
            counts[n["label"]] = counts.get(n["label"], 0) + 1
            if n["label"] == "PoliticalOffice" and not n.get("template"):
                offices_total += 1
                offices_elected += int(bool(n.get("is_elected")))
                cur = [e for e, _ in self.store.edges(nid, "HOLDS_OFFICE", "in") if e.get("current")]
                offices_with_holder += int(bool(cur))
                for e in cur:
                    if e.get("evidence_status") in holders:
                        holders[e["evidence_status"]] += 1
        return {"nodes_by_label": counts, "offices": offices_total, "elected_offices": offices_elected,
                "offices_with_current_holder": offices_with_holder, "current_holders_by_evidence": holders,
                "stages": self.state.stages(iso2)}


def _level_name(tier: str, iso2: str = "") -> str:
    return tier_name(iso2, tier)

