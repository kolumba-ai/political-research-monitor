"""Storylines: shortlist + judge placement, the hard rules, the review queue, topics, and the week grid / detail API."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polgraph.claims_api import build_router
from socialclaims import storylines as st
from socialclaims import topics as tp

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


class EveryUnitItsOwnStoryline:
    model = "scripted"

    def extract(self, system, prompt, tool):
        return {"relation": "unrelated", "storyline_id": "", "version_id": "", "differs_on": [], "confidence": 0.9,
                "rationale": "r", "storyline_label": "label", "version_label": "v", "topic": "otro"}, {}


def test_politicians_judged_in_parallel_all_land_in_one_file(tmp_path):
    pids = [f"person:MX:p{i}" for i in range(12)]
    claims = [{**_claim(f"c{i}-{n}", f"p{i}-{n}", f"Afirmación {n} del político {i}", "2026-09-01T10:00:00Z", "post:a"),
               "politician_id": pid} for i, pid in enumerate(pids) for n in range(60)]
    units = st.build_units(claims)
    results = st.run_country(st.StorylineEngine(st.StorylineStore(tmp_path), EveryUnitItsOwnStoryline(), TOPICS),
                             units, workers=12)
    saved = json.loads((tmp_path / "storylines.json").read_text())["politicians"]
    assert sorted(r["politician_id"] for r in results) == sorted(pids) and all(r["pending"] == 0 for r in results)
    assert {pid: len(saved[pid]["units"]) for pid in pids} == {pid: 60 for pid in pids}


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
    main, hospitals, waiting = grid["storylines"]  # the two said once are rows of their own, not one shared row
    assert (main["statements"], main["posts"], main["versions"], main["counter_claims"]) == (5, 5, 3, 1)
    assert main["cells"]["2026-08-31"] == {"statements": [2, 1, 1], "posts": [2, 1, 1]}
    assert main["weeks_active"] == 3 and main["accounts"] == 5
    assert main["evidence_counts"] == {"supports": 1, "contradicts": 1, "context": 0}  # p1's evidence, counted once
    assert (hospitals["label"], hospitals["statements"], hospitals["area"]) == ("33 new hospitals built", 1, "seguridad")
    assert (waiting["statements"], waiting["needs_review"]) == (1, 1)  # the unsure one stays on its own, flagged
    assert grid["totals"] == {"statements": 7, "posts": 6, "topics": 1, "storylines": 3, "repeated": 1, "recurring": 1,
                              "with_post_link": 6}
    assert grid["grouping"]["placed"] == 6 and grid["grouping"]["needs_review"] == 1
    # No topic judge has run: the subject area stands in, and the grid says so.
    assert grid["grouping"]["without_topic"] == 3 and grid["areas"] == ["seguridad"]
    (area,) = grid["topics"]
    assert (area["topic_id"], area["label"], area["judged"], area["storylines"]) == ("area:seguridad", "Seguridad", False, 3)
    assert {r["topic_id"] for r in grid["storylines"]} == {"area:seguridad"}
    # post:d carries a statement of two storylines: the topic counts it once, each storyline counts it too.
    assert (area["statements"], area["posts"], area["repeated"], area["needs_review"]) == (7, 6, 1, 1)
    assert area["cells"]["2026-09-14"] == {"statements": [4, 2, 2], "posts": [3, 2, 1]}
    assert sum(r["cells"].get("2026-09-14", {"posts": [0]})["posts"][0] for r in grid["storylines"]) == 4
    only = api.get(f"/api/claims/MX/storylines/{PID}", params={"evidence": "supports", "recurring": True}).json()
    assert [r["storyline_id"] for r in only["storylines"]] == [main["storyline_id"]] and only["topics"][0]["statements"] == 5
    assert api.get(f"/api/claims/MX/storylines/{PID}", params={"evidence": "none"}).json()["topics"][0]["storylines"] == 2
    assert api.get(f"/api/claims/MX/storylines/{PID}", params={"area": "salud"}).json()["topics"] == []
    assert api.get(f"/api/claims/MX/storylines/{PID}", params={"evidence": "maybe"}).status_code == 422

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
    single = api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": waiting["storyline_id"]}).json()["storyline"]
    assert single["statements"] == 1 and len(single["pending_review"]) == 1 and single["topic"]["judged"] is False
    assert api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": "once"}).status_code == 404
    assert full["area"] == "seguridad" and full["topic"]["topic_id"] == "area:seguridad"
    assert api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": "sl-nope"}).status_code == 404
    assert full["opinion"] is None and main["opinion"] is None  # no System D export yet

    # System D's simulated polling (opinion.json) is attached to the storyline it was linked to.
    run = {"status": "decisive", "n_completed": 80, "p_believe": 0.4, "ci_low": 0.3, "ci_high": 0.5,
           "sentiment_mean": 0.1, "social_sentiment": 0.3, "gap": -0.2, "updated_at": "2026-11-15T21:00:00Z",
           "strata": [{"region": "Centro", "age_band": "18-29", "gender": "mujer", "n": 7, "share_believe": 0.5,
                       "sentiment_mean": 0.2}]}
    (d / "opinion.json").write_text(json.dumps({"updated_at": "2026-11-15T21:05:00Z", "storylines": {
        main["storyline_id"]: {"storyline_id": main["storyline_id"], "latest": "run-2",
                               "runs": {"run-1": {**run, "status": "polling"}, "run-2": run}}}}))
    polled = api.get(f"/api/claims/MX/storylines/{PID}").json()["storylines"]
    assert polled[0]["opinion"] == {"sim_run": "run-2", **{k: run[k] for k in (
        "status", "n_completed", "p_believe", "ci_low", "ci_high", "sentiment_mean", "social_sentiment", "gap")}}
    assert polled[1]["opinion"] is None
    full = api.get(f"/api/claims/MX/storylines/{PID}", params={"storyline": main["storyline_id"]}).json()["storyline"]
    assert full["opinion"]["runs"]["run-2"]["strata"][0]["n"] == 7 and full["opinion_updated_at"] == "2026-11-15T21:05:00Z"

    # The claim list carries the same evidence, and still works when no grouping has been run.
    page = api.get(f"/api/claims/MX/politicians/{PID}").json()
    assert page["claims"][0]["evidence_counts"]["supports"] == 1 and page["claims"][0]["url"] == \
        "https://www.instagram.com/p/A/"  # the link comes from the post record
    (d / "storylines.json").unlink()
    ungrouped = api.get(f"/api/claims/MX/storylines/{PID}").json()
    assert ungrouped["grouping"]["placed"] == 0 and ungrouped["storylines"][0]["statements"] == 2  # p1 said twice
    assert [t["topic_id"] for t in ungrouped["topics"]] == ["none"] and ungrouped["topics"][0]["storylines"] == 6


class ScriptedTopicJudge:
    """Puts the homicide storyline under one topic and the hospital storyline under another; a storyline the first
    answer leaves out is asked again."""
    model = "scripted"

    def __init__(self, skip_first=False):
        self.prompts, self.skip = [], skip_first

    def extract(self, system, prompt, tool):
        self.prompts.append(prompt)
        todo = prompt.split("STORYLINES TO PLACE")[1].strip().splitlines()
        if self.skip and len(self.prompts) == 1:
            todo = todo[:1]
        existing = prompt.split("STORYLINES TO PLACE")[0]
        out = {}
        for line in todo:
            sid = line.split("]")[0].lstrip("[")
            label = "Public hospitals" if "hospitals" in line else "Homicide figures"
            tid = existing.split(f'] "{label}"')[0].rsplit("[", 1)[1] if f'"{label}"' in existing else ""
            out.setdefault(label, {"topic_id": tid, "label": label, "storyline_ids": []})["storyline_ids"].append(sid)
        first = next(iter(out.values()))["storyline_ids"][0]  # named twice: the first topic keeps it
        return {"topics": [*out.values(), {"topic_id": "", "label": "Invented", "storyline_ids": ["sl-unknown", first]}]}, {
            "input_tokens": 5, "output_tokens": 2}


def test_topic_judge_groups_storylines_and_humans_can_move_and_rename(tmp_path):
    store, units, _, _ = _run(tmp_path)
    judge = ScriptedTopicJudge(skip_first=True)
    engine = tp.TopicEngine(store, judge)
    assert len(engine.pending(PID)) == 3
    out = engine.run(PID, units[PID])
    assert (out["topics"], out["assigned"], out["opened"], out["pending"]) == (2, 3, 2, 0)
    assert out["usage"] == {"calls": 2, "input_tokens": 10, "output_tokens": 4}  # the skipped one took a second call
    assert "EXISTING TOPICS" in judge.prompts[1] and "| area seguridad | 5 statement(s)" in judge.prompts[0]
    pol = store.politician(PID)
    labels = {t["label"]: t["topic_id"] for t in pol["topics"].values()}
    assert set(labels) == {"Homicide figures", "Public hospitals"}  # the invented storyline id opened nothing
    homicides = pol["storylines"][pol["units"]["prop:p1"]["storyline_id"]]
    hospitals = next(s for s in pol["storylines"].values() if s["label"] == "33 new hospitals built")
    assert homicides["topic_id"] == labels["Homicide figures"] and homicides["topic"] == "seguridad"  # the area is kept
    assert len(homicides["versions"]) == 3  # grouping into topics does not touch the storyline
    assert engine.run(PID, units[PID])["usage"] == {} and len(judge.prompts) == 2  # nothing pending: no call

    grid = st.matrix(CLAIMS, pol)
    assert [(t["label"], t["judged"], t["storylines"], t["statements"], t["repeated"]) for t in grid["topics"]] == [
        ("Homicide figures", True, 2, 6, 1), ("Public hospitals", True, 1, 1, 0)]  # the one in review joined its subject
    assert grid["grouping"]["without_topic"] == 0 and grid["totals"]["topics"] == 2
    assert st.detail(CLAIMS, {}, pol, {}, homicides["storyline_id"])["topic"] == {
        "topic_id": labels["Homicide figures"], "label": "Homicide figures", "judged": True}
    # Filters keep whole storylines and the topic counts follow; totals describe everything collected.
    recurring = st.matrix(CLAIMS, pol, recurring=True)
    assert [t["label"] for t in recurring["topics"]] == ["Homicide figures"] and recurring["totals"]["topics"] == 2
    assert st.matrix(CLAIMS, pol, area="salud")["topics"] == []

    # Human edits: by label (case-insensitive) into an existing topic, which drops the emptied one; then a rename.
    moved = tp.set_topic(store, hospitals["storyline_id"], "homicide FIGURES")
    assert moved["topic_id"] == labels["Homicide figures"] and moved["new_topic"] is False
    assert list(pol["topics"]) == [labels["Homicide figures"]]
    opened = tp.set_topic(store, hospitals["storyline_id"], "Hospital construction")
    assert opened["new_topic"] is True and len(pol["topics"]) == 2
    tp.rename_topic(store, labels["Homicide figures"], "Homicide statistics")
    assert pol["topics"][labels["Homicide figures"]]["label"] == "Homicide statistics"
    for call in (lambda: tp.set_topic(store, "sl-nope", "X"), lambda: tp.set_topic(store, hospitals["storyline_id"], "tp-nope"),
                 lambda: tp.rename_topic(store, "tp-nope", "X")):
        try:
            call()
            raise AssertionError("expected ReviewError")
        except st.ReviewError:
            pass
    log = [json.loads(line) for line in (tmp_path / "storyline_decisions.jsonl").read_text().splitlines()]
    assert [d["status"] for d in log if d["status"].startswith("topic")] == [
        "topic_assigned", "topic_assigned", "topic_assigned", "topic_set", "topic_set", "topic_renamed"]
    assert log[-1]["judge"] == "human" and log[-6]["judge"].startswith("scripted:")

    # A storyline split off by hand stays under its topic; regrouping forgets every topic.
    st.move(store, PID, units[PID][0]["key"], None, None, units, label="Homicides down 50%")
    split = next(s for s in pol["storylines"].values() if s["label"] == "Homicides down 50%")
    assert split["topic_id"] == labels["Homicide figures"]
    # The moved unit had opened the storyline it left: that one keeps its id and its other versions.
    assert split["storyline_id"] != homicides["storyline_id"] and len(homicides["versions"]) == 3
    assert pol["storylines"][homicides["storyline_id"]] is homicides
    tp.reset(store, PID)
    assert pol["topics"] == {} and len(engine.pending(PID)) == 4
    reloaded = st.StorylineStore(tmp_path).politician(PID)
    assert reloaded["topics"] == {} and all("topic_id" not in s for s in reloaded["storylines"].values())


def test_topic_judge_looks_again_at_storylines_left_alone(tmp_path):
    store, units, _, _ = _run(tmp_path)

    class Splitter:
        """First gives every storyline a topic of its own; asked again, joins the two homicide ones and leaves the
        hospital one out of its answer."""
        model = "scripted"

        def __init__(self):
            self.prompts = []

        def extract(self, system, prompt, tool):
            self.prompts.append(prompt)
            lines = prompt.split("STORYLINES TO PLACE")[1].strip().splitlines()
            ids = [(line.split("]")[0].lstrip("["), "hospitals" in line) for line in lines]
            if "NOTE:" not in prompt:
                return {"topics": [{"topic_id": "", "label": f"Topic {i}", "storyline_ids": [sid]}
                                   for i, (sid, _) in enumerate(ids)]}, {"input_tokens": 1, "output_tokens": 1}
            return {"topics": [{"topic_id": "", "label": "Homicide figures",
                                "storyline_ids": [sid for sid, hospital in ids if not hospital]}]}, {}

    judge = Splitter()
    out = tp.TopicEngine(store, judge).run(PID, units[PID])
    assert (out["topics"], out["assigned"], out["regrouped"], out["alone"], out["pending"]) == (2, 3, 2, 1, 0)
    assert out["usage"]["calls"] == 2 and "EXISTING TOPICS" not in judge.prompts[1]  # the lone topics are not offered
    pol = store.politician(PID)
    by_topic = {}
    for s in pol["storylines"].values():
        by_topic.setdefault(pol["topics"][s["topic_id"]]["label"], []).append(s["label"])
    assert len(by_topic["Homicide figures"]) == 2 and len(pol["topics"]) == 2
    (kept, alone), = [(k, v) for k, v in by_topic.items() if k != "Homicide figures"]
    assert kept.startswith("Topic ") and alone == ["33 new hospitals built"]  # not answered: it keeps its topic
    assert st.StorylineStore(tmp_path).politician(PID)["topics"] == pol["topics"]


def test_topic_judge_failure_keeps_what_was_assigned(tmp_path):
    store, units, _, _ = _run(tmp_path)

    class Failing:
        model = "scripted"

        def extract(self, system, prompt, tool):
            raise RuntimeError("boom")

    out = tp.TopicEngine(store, Failing()).run(PID, units[PID])
    assert out["errors"] == 1 and out["pending"] == 3 and out["topics"] == 0
