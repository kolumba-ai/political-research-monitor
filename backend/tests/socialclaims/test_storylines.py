"""Storylines: shortlist + judge placement, the hard rules, the review queue, and the week grid / detail API."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polgraph.claims_api import build_router
from socialclaims import storylines as st

PID = "person:MX:claudia-sheinbaum-pardo"
TOPICS = ["seguridad", "salud", "otro"]


def _claim(cid, prop, text, when, post, speaker="politician", **extra):
    return {"claim_id": cid, "proposition_id": prop, "canonical_text": text, "text": text, "text_en": text + " (en)",
            "type": "statistic", "speaker": speaker, "politician_id": PID, "politician_name": "Claudia Sheinbaum",
            "published_at": when, "data_point_id": post, "platform": "instagram", **extra}


CLAIMS = [
    _claim("c1", "p1", "Los homicidios dolosos bajaron 50% desde septiembre de 2024", "2026-09-01T10:00:00Z", "post:a"),
    _claim("c2", "p2", "Hay una reducción de 50% en homicidios dolosos desde septiembre 2024", "2026-09-03T10:00:00Z",
           "post:b", speaker=None),
    _claim("c3", "p3", "Los homicidios dolosos bajaron 53% desde septiembre de 2024", "2026-09-16T10:00:00Z", "post:c"),
    _claim("c4", "p4", "Los homicidios dolosos no han bajado, es falso", "2026-09-17T10:00:00Z", "post:d", speaker=None),
    _claim("c5", "p5", "Se construyeron 33 hospitales nuevos", "2026-09-18T10:00:00Z", "post:d", speaker=None),
    _claim("c6", "p6", "Los homicidios dolosos en Sinaloa quizá bajaron algo", "2026-09-20T10:00:00Z", "post:e"),
    _claim("c7", "p1", "Los homicidios dolosos bajaron 50% desde septiembre de 2024", "2026-09-22T10:00:00Z", "post:f"),
]


class ScriptedJudge:
    """Answers by proposition; `same_version` for p3 is wrong on purpose (53% is not 50%)."""
    model = "scripted"

    def __init__(self):
        self.prompts = []

    def extract(self, system, prompt, tool):
        self.prompts.append(prompt)
        sl = prompt.split("CANDIDATE STORYLINES")[1].split("[sl-")[1].split("]")[0] if "[sl-" in prompt else ""
        sv = prompt.split("[sv-")[1].split("]")[0] if "[sv-" in prompt else ""
        new = prompt.split("NEW STATEMENT")[1].split("CANDIDATE")[0]
        base = {"storyline_id": f"sl-{sl}", "version_id": "", "differs_on": [], "confidence": 0.95, "rationale": "r",
                "storyline_label": "Intentional homicides have fallen", "version_label": "v", "topic": "seguridad"}
        if "50%" in new and sl:
            return {**base, "relation": "same_version", "version_id": f"sv-{sv}"}, {"input_tokens": 10}
        if "53%" in new:
            return {**base, "relation": "same_version", "version_id": f"sv-{sv}"}, {}
        if "falso" in new:
            return {**base, "relation": "counter_claim"}, {}
        if "Sinaloa" in new:
            return {**base, "relation": "same_storyline_different_version", "confidence": 0.4}, {}
        label = "33 new hospitals built" if "hospitales" in new else base["storyline_label"]
        return {**base, "relation": "unrelated", "storyline_id": "", "storyline_label": label}, {}


def _run(tmp_path):
    store = st.StorylineStore(tmp_path)
    units = st.build_units(CLAIMS)
    judge = ScriptedJudge()
    result = st.StorylineEngine(store, judge, TOPICS).run(PID, units[PID])
    return store, units, judge, result


def test_judge_places_units_and_hard_rules_override_it(tmp_path):
    store, units, judge, result = _run(tmp_path)
    pol = store.politician(PID)
    main = pol["storylines"][pol["units"]["prop:p1"]["storyline_id"]]
    kinds = {k: (v["kind"], v["units"]) for k, v in main["versions"].items()}

    assert pol["units"]["prop:p2"]["version_id"] == pol["units"]["prop:p1"]["version_id"]  # reworded, same figures
    # The judge called 53% the same version as 50%; the figures differ, so it is a version of its own.
    assert pol["units"]["prop:p3"]["storyline_id"] == main["storyline_id"]
    assert pol["units"]["prop:p3"]["version_id"] != pol["units"]["prop:p1"]["version_id"]
    assert pol["units"]["prop:p3"]["rule_blocks"] == ["values"]
    assert kinds[pol["units"]["prop:p4"]["version_id"]] == ("counter_claim", ["prop:p4"])
    assert pol["units"]["prop:p5"]["storyline_id"] != main["storyline_id"]  # same post, different claim
    # Low confidence: not applied, kept on its own, waiting in the review queue.
    assert pol["units"]["prop:p6"]["storyline_id"] != main["storyline_id"]
    assert [p["unit"] for p in pol["review"].values()] == ["prop:p6"]
    assert result["needs_review"] == 1 and result["applied"] == 5 and result["rule_blocked"] == 1
    assert result["pending"] == 0 and result["usage"]["calls"] == 6  # one call per unit; p1 has two statements
    assert "[sl-" not in judge.prompts[0] and "53%" in judge.prompts[2]

    log = [json.loads(line) for line in (tmp_path / "storyline_decisions.jsonl").read_text().splitlines()]
    assert len(log) == 6 and all(d["judge"].startswith("scripted:") and d["rationale"] for d in log)
    assert log[2]["judge_relation"] == "same_version" and log[2]["relation"] == "same_storyline_different_version"

    # A second run places nothing new and keeps every label and id.
    before = json.loads((tmp_path / "storylines.json").read_text())["politicians"]
    again = st.StorylineEngine(st.StorylineStore(tmp_path), ScriptedJudge(), TOPICS).run(PID, units[PID])
    assert again["usage"] == {} and json.loads((tmp_path / "storylines.json").read_text())["politicians"] == before


def test_review_accept_reject_move_rename(tmp_path):
    store, units, _, _ = _run(tmp_path)
    pol = store.politician(PID)
    main = pol["units"]["prop:p1"]["storyline_id"]
    proposal = next(iter(pol["review"]))
    assert st.review_queue(store, units)[0]["proposed_storyline_label"] == "Intentional homicides have fallen"

    st.accept(store, proposal, units)
    assert pol["units"]["prop:p6"]["storyline_id"] == main and pol["units"]["prop:p6"]["judge"] == "human"
    assert not pol["review"] and len(pol["storylines"]) == 2

    # A human cannot merge different figures into one version either.
    d = st.move(store, PID, "prop:p3", main, pol["units"]["prop:p1"]["version_id"], units)
    assert d["rule_blocks"] == ["values"] and pol["units"]["prop:p3"]["version_id"] != pol["units"]["prop:p1"]["version_id"]
    st.move(store, PID, "prop:p4", None, None, units)  # detach into a storyline of its own
    assert pol["units"]["prop:p4"]["storyline_id"] not in (main, pol["units"]["prop:p5"]["storyline_id"])
    st.rename(store, main, "Homicides down since Sept 2024")
    reloaded = st.StorylineStore(tmp_path).politician(PID)
    assert reloaded["storylines"][main]["label"] == "Homicides down since Sept 2024"
    statuses = [json.loads(line)["status"] for line in (tmp_path / "storyline_decisions.jsonl").read_text().splitlines()]
    assert statuses[-4:] == ["accepted", "moved", "moved", "renamed"]


def test_structured_fields_block_place_period_and_negation():
    def unit(**f):
        return {"key": "k", "text": "x 50", "facets": {"vals": [{"value": "50", "unit": "%"}], "negated": False,
                                                       "place": "México", "period_start": "2024-09-01", **f}}
    assert st.blocked(unit(), unit()) == []
    assert st.blocked(unit(), unit(place="Sinaloa", negated=True, period_start="2025-01-01")) == \
        ["negation", "place", "period"]
    assert st.blocked(unit(), unit(vals=[{"value": "53", "unit": "%"}], place=None)) == ["values"]


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_week_grid_and_detail_keep_every_post_link_and_evidence(tmp_path):
    d = tmp_path / "MX"
    d.mkdir()
    _run(d)
    posts = [{"data_point_id": f"post:{x}", "platform": "instagram", "platform_id": x.upper(),
              "url": None if x == "f" else f"https://www.instagram.com/p/{x.upper()}/", "uploader_handle": f"acct_{x}",
              "oriane_content_id": f"cnt_{x}", "published_at": "2026-09-01T10:00:00Z", "engagement": {"views": 9}}
             for x in "abcdef"]
    _write(d / "claims.jsonl", CLAIMS)
    _write(d / "posts.jsonl", posts)
    _write(d / "documents.jsonl", [{"document_datapoint": "doc-1-v1", "url": "https://gob.mx/x", "publisher": "gob.mx",
                                    "title": "Informe", "authority_tier": 1, "published_at": "2026-09-02T00:00:00Z"}])
    _write(d / "evidence.jsonl", [
        {"link_id": "link:1", "proposition_id": "p1", "politician_id": PID, "document_datapoint": "doc-1-v1",
         "stance": "supports", "confidence": 0.9, "quote": "50%", "rationale": "says so", "authority_tier": 1},
        {"link_id": "link:2", "proposition_id": "p1", "politician_id": PID, "document_datapoint": "doc-gone-v1",
         "stance": "contradicts", "confidence": 0.5, "quote": None, "rationale": "other figure", "authority_tier": 3}])
    (d / "manifest.json").write_text(json.dumps({"pulled_at": "2026-11-15T00:00:00Z"}))
    app = FastAPI()
    app.include_router(build_router(lambda: tmp_path))
    api = TestClient(app)

    grid = api.get(f"/api/claims/MX/storylines/{PID}").json()
    assert grid["weeks"] == ["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21"]  # the empty week is kept
    main, once = grid["storylines"]
    assert (main["statements"], main["posts"], main["versions"], main["counter_claims"]) == (5, 5, 3, 1)
    assert main["cells"]["2026-08-31"] == {"statements": [2, 1, 1], "posts": [2, 1, 1]}
    assert main["weeks_active"] == 3 and main["accounts"] == 5
    assert main["evidence_counts"] == {"supports": 1, "contradicts": 1, "context": 0}  # p1's evidence, counted once
    assert once["storyline_id"] == "once" and once["statements"] == 2 and once["needs_review"] == 1
    assert grid["totals"] == {"statements": 7, "posts": 6, "storylines": 1, "recurring": 1, "with_post_link": 6}
    assert grid["grouping"]["placed"] == 6 and grid["grouping"]["needs_review"] == 1

    full = api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": main["storyline_id"]}).json()["storyline"]
    assert [v["kind"] for v in full["versions"]] == ["claim", "claim", "counter_claim"]
    first = full["versions"][0]
    assert [p["url"] for p in first["post_list"]] == [
        "https://www.instagram.com/p/A/", "https://www.instagram.com/p/B/", None]
    assert first["post_list"][2]["platform_id"] == "F" and first["post_list"][2]["uploader_handle"] == "acct_f"
    s = first["post_list"][0]["statements"][0]
    assert s["placement"]["judge"].startswith("scripted:") and s["evidence_counts"] == {
        "supports": 1, "contradicts": 1, "context": 0}
    assert s["evidence"][0]["document"]["url"] == "https://gob.mx/x" and s["evidence"][1]["document"] is None
    assert first["post_list"][1]["statements"][0]["evidence"] == []  # p2 has its own (no) evidence
    assert full["evidence_counts"]["supports"] == 1 and full["statements"] == 5

    week = api.get(f"/api/claims/MX/storylines/{PID}",
                   params={"storyline": main["storyline_id"], "week": "2026-09-14"}).json()["storyline"]
    assert week["statements"] == 2 and [v["statements"] for v in week["versions"]] == [1, 1]
    singles = api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": "once"}).json()["storyline"]
    assert singles["statements"] == 2 and len(singles["pending_review"]) == 1
    assert api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": "sl-nope"}).status_code == 404

    # The claim list carries the same evidence, and still works when no grouping has been run.
    page = api.get(f"/api/claims/MX/politicians/{PID}").json()
    assert page["claims"][0]["evidence_counts"]["supports"] == 1 and page["claims"][0]["url"] == \
        "https://www.instagram.com/p/A/"  # the link comes from the post record
    (d / "storylines.json").unlink()
    ungrouped = api.get(f"/api/claims/MX/storylines/{PID}").json()
    assert ungrouped["grouping"]["placed"] == 0 and ungrouped["storylines"][0]["statements"] == 2  # p1 said twice
