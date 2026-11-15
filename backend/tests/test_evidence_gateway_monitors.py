import hashlib
import hmac

import pytest

from polgraph.evidence import (EvidenceStatus, authority_tier, classify, evaluate_pages, mentions_name,
                               office_keywords)
from polgraph.exa_gateway import BudgetExceeded, ExaGateway, urls_for_path
from polgraph.monitors import verify_signature
from polgraph.research import label_near_country


def test_mentions_name_accent_and_order_tolerant():
    assert mentions_name("Claudia Sheinbaum Pardo", "la presidenta CLAUDIA SHEINBAUM encabezó")
    assert mentions_name("Clara Brugada Molina", "Jefa de Gobierno, Clara Marina Brugada")
    assert not mentions_name("Claudia Sheinbaum Pardo", "Sheinbaum dijo")  # surname alone is not enough
    assert not mentions_name("Juan Pérez", "Juan García y Pedro Pérez")


def test_office_keywords_gender_neutral():
    kw = office_keywords("President of Mexico", "Presidenta de México")
    assert "president" in kw
    kw = office_keywords("Head of Government of Mexico City", "Jefa de Gobierno")
    assert "jef" in kw and "government" in kw


def test_authority_tiers():
    assert authority_tier("https://www.gob.mx/presidencia") == 1
    assert authority_tier("https://congresojal.gob.mx") == 1
    assert authority_tier("https://freedomhouse.org/country/mexico") == 1
    assert authority_tier("https://es.wikipedia.org/wiki/X") == 2
    assert authority_tier("https://randomblog.net/x") == 3


def test_classify_thresholds():
    from polgraph.evidence import SupportingSource as S
    assert classify([], 2) == EvidenceStatus.unsupported
    assert classify([S("u", None, "blog.net", 3, "x")], 2) == EvidenceStatus.single_source
    assert classify([S("u", None, "gob.mx", 1, "x")], 2) == EvidenceStatus.official_single
    assert classify([S("u", None, "gob.mx", 1, "x"), S("v", None, "gob.mx", 1, "y")], 2) == EvidenceStatus.official_single
    assert classify([S("u", None, "gob.mx", 1, "x"), S("v", None, "reuters.com", 2, "y")], 2) == EvidenceStatus.verified


def test_context_terms_block_wrong_jurisdiction():
    pages = {"u": {"url": "https://a.mx/x", "text": "Pablo Lemus, gobernador de Jalisco"}}
    assert evaluate_pages("Pablo Lemus Navarro", ["gobernador"], pages, "t", ["Jalisco"])
    assert not evaluate_pages("Pablo Lemus Navarro", ["gobernador"], pages, "t", ["Colima"])


def test_label_near_country_requires_proximity():
    text = "Mexico " + "x " * 600 + "Norway liberal democracy"
    assert not label_near_country(text, "liberal_democracy", ["Mexico"])
    assert label_near_country("Mexico: Status Partly Free", "partly_free", ["Mexico"])
    assert not label_near_country("Mexico: Status Partly Free", "free", ["Mexico"])  # 'free' inside 'partly free'


def test_urls_for_path_nested():
    run = {"output": {"grounding": [
        {"field": "bodies[2].offices[1].current_holders[0].full_name", "citations": [{"url": "https://a"}]},
        {"field": "bodies[0].offices[1]", "citations": [{"url": "https://b"}]},
        {"field": "bodies[2].offices[12].title_en", "citations": [{"url": "https://c"}]}]}}
    assert urls_for_path(run, [("bodies", 2), ("offices", 1)]) == ["https://a"]


def test_gateway_caches_and_records_usage(gateway, fake_exa):
    r1 = gateway.search("Claudia Sheinbaum presidenta", purpose="t")
    r2 = gateway.search("Claudia Sheinbaum presidenta", purpose="t")
    assert r1 == r2 and sum(1 for c in fake_exa.calls if c[0] == "search") == 1
    rep = gateway.usage_report()
    assert rep["this_run"]["cache_hits"] == 1 and rep["lifetime_cost_usd"] == pytest.approx(0.007)


def test_gateway_retries_on_429(settings):
    from .fakes import FakeExa
    sleeps = []
    gw = ExaGateway(settings, client=FakeExa(fail_first=2), sleep=sleeps.append)
    gw.search("Pablo Lemus", purpose="t")
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_gateway_does_not_retry_on_400(settings):
    class Bad:
        def search(self, *a, **k):
            raise ValueError("Request failed with status code 400: bad")
    gw = ExaGateway(settings, client=Bad(), sleep=lambda s: None)
    with pytest.raises(ValueError):
        gw.search("x", purpose="t")


def test_gateway_budget_enforced(settings, fake_exa):
    settings.exa_run_budget_usd = 1.0
    gw = ExaGateway(settings, client=fake_exa, sleep=lambda s: None)
    with pytest.raises(BudgetExceeded):
        gw.agent_run("q", purpose="democracy:MX", output_schema={"type": "object"}, max_cost_usd=2.0)
    assert fake_exa.calls == []


def test_agent_runs_are_cached(gateway, fake_exa):
    a = gateway.agent_run("q", purpose="democracy:MX", output_schema={"type": "object"})
    b = gateway.agent_run("q", purpose="democracy:MX", output_schema={"type": "object"})
    assert a["id"] == b["id"] and len(fake_exa.agent.runs.created) == 1


def test_webhook_signature():
    secret, body, ts = "whsec", b'{"a":1}', "1700000000"
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert verify_signature(body, f"t={ts},v1={sig}", secret, now=1700000100)
    assert not verify_signature(body + b" ", f"t={ts},v1={sig}", secret, now=1700000100)
    assert not verify_signature(body, f"t={ts},v1={sig}", secret, now=1800000000)  # replay window
    assert not verify_signature(body, None, secret)


def test_mentions_name_with_dropped_prefix():
    assert mentions_name("Muhammad Shehbaz Sharif", "Prime Minister Shehbaz Sharif said on Monday")
    assert mentions_name("Syed Murad Ali Shah", "Sindh Chief Minister Murad Ali Shah")
    assert not mentions_name("Muhammad Shehbaz Sharif", "Muhammad Ishaq Dar said")
