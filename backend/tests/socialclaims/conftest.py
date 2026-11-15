"""System B fixtures: a tiny System A knowledge_base export, a Postgres test DB, and a fake Oriane server."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
import psycopg
import pytest

from socialclaims.config import SocialSettings
from socialclaims.db import connect, migrate

TEST_DB = os.environ.get("SOCIALCLAIMS_TEST_DATABASE_URL", "postgresql://ubuntu:ubuntu@localhost:5432/socialclaims_test")
RUN = "run-MX-20261114T230338Z"


def _holding(src, dst, term, status="verified", current=True, start="2024-10-01", end="2030-09-30", conf=0.99):
    return {"src": src, "type": "HOLDS_OFFICE", "dst": dst, "current": current, "status": "current" if current else "former",
            "start": start, "end": end, "is_acting": False, "evidence_status": status, "confidence": conf,
            "term_id": term, "pipeline_mode": "research_only", "pipeline_run": RUN}


def kb_graph() -> tuple[list[dict], list[dict]]:
    nodes = [
        {"id": "country:MX", "label": "Country", "iso2": "MX", "name": "Mexico", "eligible": False,
         "research_status": "research_only_enriched"},
        {"id": "level:MX:national", "label": "GovernmentLevel", "name": "Federal", "tier": "national", "country": "MX"},
        {"id": "level:MX:state:MX-CMX", "label": "GovernmentLevel", "name": "Ciudad de México", "tier": "state",
         "iso_3166_2": "MX-CMX", "country": "MX"},
        {"id": "level:MX:state:MX-MIC", "label": "GovernmentLevel", "name": "Michoacán", "tier": "state",
         "iso_3166_2": "MX-MIC", "country": "MX"},
        {"id": "body:MX:state:MX-MIC:executive", "label": "GovernmentBody", "name": "Executive of Michoacán"},
        {"id": "source:gob", "label": "Source", "url": "https://www.gob.mx/presidencia"},
        {"id": "body:MX:national:executive", "label": "GovernmentBody", "name": "Executive"},
        {"id": "body:MX:state:MX-CMX:executive", "label": "GovernmentBody", "name": "Executive of CDMX"},
        {"id": "office:MX:national:executive:president", "label": "PoliticalOffice", "name": "President",
         "title_local": "Presidenta de los Estados Unidos Mexicanos", "keywords": ["presidenta"]},
        {"id": "office:MX:state:MX-CMX:executive:head-of-government", "label": "PoliticalOffice",
         "name": "Head of Government of Ciudad de México", "title_local": "Jefa de Gobierno", "keywords": ["jefa de gobierno"]},
        {"id": "office:MX:state:MX-MIC:executive:governor", "label": "PoliticalOffice", "name": "Governor of Michoacán"},
        {"id": "person:MX:claudia-sheinbaum-pardo", "label": "Person", "country": "MX", "name": "Claudia Sheinbaum Pardo",
         "aliases": [], "updated_at": "2026-11-14T23:07:40+00:00", "last_verified": "2026-11-14T23:07:40+00:00"},
        {"id": "person:MX:clara-marina-brugada-molina", "label": "Person", "country": "MX",
         "name": "Clara Marina Brugada Molina", "aliases": ["Clara Brugada"], "updated_at": "2026-11-14T23:14:44+00:00"},
        {"id": "person:MX:alfredo-ramirez-bedolla", "label": "Person", "country": "MX", "name": "Alfredo Ramírez Bedolla",
         "aliases": [], "updated_at": "2026-11-14T23:12:39+00:00"},
        {"id": "person:MX:former-official", "label": "Person", "country": "MX", "name": "Andrés Manuel López Obrador",
         "aliases": [], "updated_at": "2026-11-14T23:00:00+00:00"},
        {"id": "party:MX:morena", "label": "PoliticalParty", "name": "Morena", "acronym": "MORENA", "aliases": ["Morena"]},
        {"id": "term:t1", "label": "Term", "start": "2024-10-01", "end": "2030-09-30", "evidence_status": "verified"},
        {"id": "term:t2", "label": "Term", "start": "2024-10-05", "end": "2030-10-04", "evidence_status": "verified"},
        {"id": "term:t3", "label": "Term", "start": "2021-10-01", "end": "2027-09-30", "evidence_status": "single_source"},
        {"id": "term:t4", "label": "Term", "start": "2018-12-01", "end": "2024-09-30", "evidence_status": "verified"},
    ]
    edges = [
        {"src": "country:MX", "type": "HAS_GOVERNMENT_LEVEL", "dst": "level:MX:national"},
        {"src": "level:MX:national", "type": "CONTAINS", "dst": "level:MX:state:MX-CMX"},
        {"src": "level:MX:national", "type": "CONTAINS", "dst": "level:MX:state:MX-MIC"},
        {"src": "level:MX:state:MX-MIC", "type": "HAS_BODY", "dst": "body:MX:state:MX-MIC:executive"},
        {"src": "body:MX:state:MX-MIC:executive", "type": "HAS_OFFICE", "dst": "office:MX:state:MX-MIC:executive:governor"},
        {"src": "office:MX:national:executive:president", "type": "HAS_TERM", "dst": "term:t1"},
        {"src": "term:t1", "type": "TERM_HELD_BY", "dst": "person:MX:claudia-sheinbaum-pardo"},
        {"src": "term:t1", "type": "SUPPORTED_BY", "dst": "source:gob", "snippet": "Presidenta Claudia Sheinbaum"},
        {"src": "level:MX:national", "type": "HAS_BODY", "dst": "body:MX:national:executive"},
        {"src": "level:MX:state:MX-CMX", "type": "HAS_BODY", "dst": "body:MX:state:MX-CMX:executive"},
        {"src": "body:MX:national:executive", "type": "HAS_OFFICE", "dst": "office:MX:national:executive:president"},
        {"src": "body:MX:state:MX-CMX:executive", "type": "HAS_OFFICE",
         "dst": "office:MX:state:MX-CMX:executive:head-of-government"},
        _holding("person:MX:claudia-sheinbaum-pardo", "office:MX:national:executive:president", "term:t1"),
        _holding("person:MX:clara-marina-brugada-molina", "office:MX:state:MX-CMX:executive:head-of-government", "term:t2",
                 status="official_single", start="2024-10-05", end="2030-10-04", conf=0.9),
        _holding("person:MX:alfredo-ramirez-bedolla", "office:MX:state:MX-MIC:executive:governor", "term:t3",
                 status="single_source", start="2021-10-01", end="2027-09-30", conf=0.55),
        _holding("person:MX:former-official", "office:MX:national:executive:president", "term:t4", current=False,
                 start="2018-12-01", end="2024-09-30"),
        {"src": "person:MX:claudia-sheinbaum-pardo", "type": "MEMBER_OF_PARTY", "dst": "party:MX:morena",
         "coalition": "Sigamos Haciendo Historia", "as_of": "2026-11-14T23:07:40+00:00"},
    ]
    return nodes, edges


def write_kb(path, nodes, edges):
    (path / "graph").mkdir(parents=True, exist_ok=True)
    (path / "graph" / "nodes.jsonl").write_text("\n".join(json.dumps(n) for n in nodes) + "\n")
    (path / "graph" / "edges.jsonl").write_text("\n".join(json.dumps(e) for e in edges) + "\n")
    (path / "manifest.json").write_text(json.dumps({"format_version": 1}))
    return path


@pytest.fixture
def kb_dir(tmp_path):
    nodes, edges = kb_graph()
    return write_kb(tmp_path / "kb", nodes, edges)


@pytest.fixture
def graph(kb_dir):
    from socialclaims.systema import KnowledgeBaseGraph
    return KnowledgeBaseGraph(kb_dir)


@pytest.fixture
def sc_settings(tmp_path, kb_dir):
    return SocialSettings(_env_file=None, database_url=TEST_DB, system_a_kb_dir=kb_dir, oriane_api_key="test-key",
                          raw_storage_dir=tmp_path / "raw", oriane_monthly_credit_ceiling=10_000,
                          oriane_run_credit_ceiling=4_000)


@pytest.fixture
def conn():
    try:
        c = connect(TEST_DB)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres test database unavailable: {exc}")
    with c.transaction():
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(c)
    yield c
    c.close()


def make_result(i: int, matched=("written",), platform="tiktok", published="2026-11-10T12:00:00Z", **kw) -> dict:
    r = {"id": f"cnt_{i:028d}", "platform": platform, "platformId": f"7{i:018d}", "format": "video",
         "profileId": f"prf_{i:028d}", "profileHandle": f"creator{i}", "caption": "Claudia Sheinbaum habla de Morena",
         "captionLanguage": "es", "hashtags": ["#cdmx"], "publishedAt": published, "createdAt": "2026-11-11T00:00:00Z",
         "updatedAt": "2026-11-12T00:00:00Z", "matchedQueries": list(matched), "viewsCount": 1000 + i, "likesCount": 10,
         "sharesCount": 1, "commentsCount": 2, "interactionsCount": 13, "engagementRatePerViews": 1.3,
         "locationCountryCode": "MX", "locationCountryConfidence": 0.9, "profileEmails": ["x@example.com"],
         "media": [{"type": "video", "transcript": "hola", "transcriptLanguage": "es",
                    "onScreenText": ["texto en pantalla"], "frames": [{"onScreenTextRegions": []}]}]}
    r.update(kw)
    return r


class FakeOriane:
    """Serves `results` with real offset/limit/id.excludes semantics; can be scripted to fail."""

    def __init__(self, results: list[dict], fail: dict[int, int] | None = None, partial: bool = False):
        self.results, self.fail, self.partial = results, dict(fail or {}), partial
        self.calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = dict(request.url.params)
        n = len(self.calls)
        self.calls.append({"params": params, "body": body, "path": request.url.path,
                           "auth": request.headers.get("authorization")})
        meta = {"requestId": f"req_{n:028d}", "executionTime": 5, "timestamp": 1794605307269}
        if n in self.fail:
            code = self.fail[n]
            return httpx.Response(code, json={"metadata": meta, "error": {"message": f"scripted {code}"}})
        excl = set(((body.get("filters") or {}).get("id") or {}).get("excludes") or [])
        pool = [r for r in self.results if r["id"] not in excl]
        off, lim = int(params["offset"]), int(params["limit"])
        if off > 0 and off >= len(pool):
            return httpx.Response(416, json={"metadata": meta, "error": {"message": "offset past end"}})
        page = pool[off:off + lim]
        meta["pagination"] = {"offset": off, "limit": lim, "totalCount": len(pool)}
        return httpx.Response(206 if self.partial else 200,
                              json={"metadata": meta, "data": {"aggregations": {"platform": {}}, "results": page}})

    def client(self):
        from socialclaims.oriane import OrianeClient
        return OrianeClient("test-key", "https://connect.test", transport=httpx.MockTransport(self.handler),
                            sleep=lambda s: None)


FIXED_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
