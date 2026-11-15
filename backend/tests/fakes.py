"""Test-only stand-in for the exa_py client. Never used by the production pipeline."""
from __future__ import annotations

import itertools
from types import SimpleNamespace

FH = "https://freedomhouse.org/country/mexico/freedom-world/2026"
VDEM = "https://www.v-dem.net/documents/democracy_report_2026.pdf"
PRES_GOB = "https://www.gob.mx/presidencia"
PRES_NEWS = "https://www.reuters.com/world/americas/sheinbaum-sworn-in"
JAL_GOB = "https://jalisco.gob.mx/gobernador"
JAL_NEWS = "https://elpais.com/mexico/jalisco-governor"
FAKE_GOV_PAGE = "https://example-blog.com/random"
LAW = "https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf"
JAL_CONGRESS = "https://www.congresojal.gob.mx/diputados"

PAGES = {
    FH: "Mexico: Freedom in the World 2026 Country Report. Status: Partly Free. Global Freedom Score 58/100.",
    VDEM: "Table 1. ... Mexico electoral democracy (lower bound) ... Brazil electoral democracy ...",
    PRES_GOB: "La Presidenta de México, Claudia Sheinbaum Pardo, encabezó la ceremonia.",
    PRES_NEWS: "Claudia Sheinbaum was sworn in as President of Mexico on October 1, 2024.",
    JAL_GOB: "Pablo Lemus Navarro, Gobernador del Estado de Jalisco, inauguró...",
    JAL_NEWS: "Jalisco governor Pablo Lemus said on Monday...",
    FAKE_GOV_PAGE: "Nothing relevant here about anyone.",
    JAL_CONGRESS: "Members of the Congress of Jalisco: District JD-5 Ana Lopez Perez (Morena); "
                  "reserved list: Maria Torres Diaz.",
    LAW: "Artículo 8o. Los funcionarios y empleados públicos respetarán el ejercicio del derecho de petición...",
}

STRUCTURED = {
    "assembly": {"total_seats": 38, "general_seats": 20, "general_seat_selection_method": "direct_election",
                 "constituency_prefix": "JD", "constituency_first": 1, "constituency_last": 20, "reserved_women": 2,
                 "reserved_selection_method": "indirect_election", "official_members_url": JAL_CONGRESS},
    "legislators": {"members": [
        {"full_name": "Ana López Pérez", "constituency_code": "jd - 5", "seat_type": "general", "party_acronym": "Morena",
         "source_urls": [JAL_CONGRESS]},
        {"full_name": "María Torres Díaz", "seat_type": "women_reserved", "source_urls": [JAL_CONGRESS]},
        {"full_name": "Invented Member Name", "constituency_code": "JD-7", "seat_type": "general",
         "source_urls": [FAKE_GOV_PAGE]},
        {"full_name": "unknown", "constituency_code": "JD-8"},
    ]},
    "democracy": {
        "assessments": [
            {"index": "Freedom House", "classification_raw": "Partly Free", "score": 58, "assessment_year": 2026,
             "evidence_url": FH},
            {"index": "V-Dem", "classification_raw": "Electoral Democracy (lower bound)", "assessment_year": 2026,
             "evidence_url": VDEM},
        ]
    },
    "contact": {
        "dimensions": [
            {"purpose": "issue_communication", "channel": "email", "sender": "organization",
             "recipient": "elected_official", "status": "allowed", "legal_basis": "CPEUM Art. 8", "evidence_url": LAW},
            {"purpose": "fact_verification_request", "channel": "email", "sender": "organization",
             "recipient": "elected_official", "status": "allowed", "legal_basis": "CPEUM Art. 8", "evidence_url": LAW},
        ],
        "laws": [{"name": "Constitución Política", "category": "constitutional", "url": LAW}],
    },
    "national": {
        "system_of_government": "Federal presidential republic",
        "bodies": [{
            "name_en": "Presidency", "name_local": "Presidencia de la República", "branch": "executive",
            "offices": [{
                "title_en": "President of Mexico", "title_local": "Presidente de los Estados Unidos Mexicanos",
                "selection_method": "direct_election", "seats": 1, "term_length_years": 6,
                "keywords": ["presidenta", "president"],
                "current_holders": [{"full_name": "Claudia Sheinbaum Pardo", "party_name": "Movimiento Regeneración Nacional",
                                     "party_acronym": "Morena", "term_start": "2024-10-01", "term_end": "2030-09-30",
                                     "source_urls": [PRES_GOB]}],
                "source_urls": [PRES_GOB],
            }],
        }],
        "subnational_levels": [{"name_en": "States", "tier": "state", "count": 32},
                               {"name_en": "Municipalities", "tier": "municipal", "count": 2475}],
    },
    "divisions": {
        "divisions": [
            {"name": "Jalisco", "executive_title_en": "Governor", "executive_title_local": "Gobernador",
             "executive_selection_method": "direct_election",
             "officeholder": {"full_name": "Pablo Lemus Navarro", "party_acronym": "MC", "party_name": "Movimiento Ciudadano",
                              "term_start": "2024-12-06", "source_urls": [JAL_GOB]},
             "legislature_name_en": "Congress of Jalisco", "legislature_seats": 38},
            {"name": "Atlantis", "executive_title_en": "Governor", "executive_selection_method": "direct_election",
             "officeholder": {"full_name": "Made Up Person", "source_urls": [FAKE_GOV_PAGE]}},
            {"name": "Colima", "executive_title_en": "Governor", "executive_selection_method": "direct_election",
             "officeholder": {"full_name": "Ficticia Inventada Ruiz", "source_urls": [FAKE_GOV_PAGE]}},
        ]
    },
}

SEARCH_RESULTS = {
    "Claudia Sheinbaum": [{"url": PRES_NEWS, "title": "Sheinbaum sworn in", "text": PAGES[PRES_NEWS]}],
    "Pablo Lemus": [{"url": JAL_NEWS, "title": "Jalisco governor", "text": PAGES[JAL_NEWS]}],
}


class _Runs:
    def __init__(self, parent):
        self.parent = parent
        self.counter = itertools.count(1)
        self.created = []

    def create(self, **kw):
        purpose = kw["metadata"]["purpose"].split(":")[0]
        rid = f"run_{next(self.counter)}"
        self.created.append((rid, purpose, kw))
        self.parent.calls.append(("agent.create", purpose))
        return SimpleNamespace(id=rid, _purpose=purpose)

    def poll_until_finished(self, run_id, **kw):
        purpose = next(p for r, p, _ in self.created if r == run_id)
        data = {"id": run_id, "status": "completed", "stop_reason": "completed",
                "output": {"text": "", "structured": STRUCTURED.get(purpose, {}), "grounding": []},
                "cost_dollars": {"total": 0.5}}
        return SimpleNamespace(model_dump=lambda **_: data)


class FakeExa:
    def __init__(self, fail_first: int = 0):
        self.calls = []
        self.fail_first = fail_first
        self.agent = SimpleNamespace(runs=_Runs(self))

    def search(self, query, **kw):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ValueError("Request failed with status code 429: rate limited")
        self.calls.append(("search", query))
        res = []
        for key, items in SEARCH_RESULTS.items():
            if key.lower() in query.lower():
                res += items
        return SimpleNamespace(results=[SimpleNamespace(published_date=None, author=None, highlights=[], **r)
                                        for r in res],
                               cost_dollars=SimpleNamespace(total=0.007), request_id="req")

    def get_contents(self, urls, **kw):
        self.calls.append(("contents", tuple(urls)))
        return SimpleNamespace(
            results=[SimpleNamespace(url=u, title=u, text=PAGES[u], published_date=None, author=None, highlights=[])
                     for u in urls if u in PAGES],
            cost_dollars=SimpleNamespace(total=0.001 * len(urls)), request_id="req")
