"""Perception labelling: prompt, parsing, the dry-run estimate, stored labels, and their export into the metrics API."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polgraph.metrics_api import build_router
from socialclaims import metrics as m
from socialclaims import perception as pc
from socialclaims.capture import Capturer
from socialclaims.segments import process_politician

from .conftest import FIXED_NOW, FakeOriane, make_result

PID = "person:MX:claudia-sheinbaum-pardo"
SPAM = "arriba la transformación siempre"


class FakeLLM:
    model = "fake-model"

    def __init__(self, fail_on: str | None = None):
        self.prompts, self.fail_on = [], fail_on

    def extract(self, system, prompt, tool):
        assert tool["name"] == "label_post" and "presentation" in system
        self.prompts.append(prompt)
        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError("model unavailable")
        n = prompt.count("\n", prompt.index("Comments:")) if "Comments:\n" in prompt else 0
        comments = [{"n": i, "stance": "for" if i == 1 else "against", "generic": i == 2} for i in range(1, n + 1)]
        return {"relevant": "namesake" not in prompt, "presentation": "positive", "reason": "praises  her programme",
                "comments": comments}, {"input_tokens": 100, "output_tokens": 20}


def _comment(text):
    return {"content": text, "publishedAt": "2026-11-11T00:00:00Z"}


def _seed(conn, graph, sc_settings):
    media = [{"type": "video", "transcriptLanguage": "es",
              "transcriptChunks": [{"startSeconds": "0.5", "endSeconds": "3.9", "text": "Inauguramos el hospital"}],
              "frames": [{"timestampSeconds": str(i), "onScreenTextRegions": [{"text": "EN VIVO"}]} for i in range(4)]}]
    posts = [make_result(1, media=media, popularComments=[_comment("Gran trabajo presidenta"), _comment(SPAM), _comment("pésimo")]),
             make_result(2, media=[], caption="Claudia Sheinbaum namesake", popularComments=[_comment(SPAM)]),
             make_result(3, media=[], popularComments=[_comment(SPAM)])]
    Capturer(conn, graph, FakeOriane(posts).client(), sc_settings, clock=lambda: FIXED_NOW).capture(PID)
    process_politician(conn, graph, sc_settings.raw_storage_dir, PID, now=FIXED_NOW)
    return [p["id"] for p in posts]


def test_prompt_and_parse():
    segments = [{"source": "onscreen", "text": "EN VIVO"}, {"source": "onscreen", "text": "EN  VIVO"},
                {"source": "speech", "text": "Hola"}, {"source": "comment", "text": " Bien ", "segment_id": "s1"},
                {"source": "comment", "text": SPAM.upper(), "segment_id": "s2"}, {"source": "comment", "text": " ", "segment_id": "s3"}]
    prompt, numbered = pc.build_prompt("Claudia Sheinbaum", {"platform": "tiktok", "profile_handle": "h", "caption": "c",
                                                             "own_account": True, "published_at": FIXED_NOW}, segments)
    assert "On-screen text: EN VIVO\n" in prompt and "own account" in prompt and prompt.endswith(f"2. {SPAM.upper()}")
    assert [c["segment_id"] for c in numbered] == ["s1", "s2"]  # blank comments are not sent
    label, rows = pc.parse({"relevant": True, "presentation": "negative", "reason": "x",
                            "comments": [{"n": 1, "stance": "for", "generic": True}]}, numbered, {SPAM})
    assert label["presentation"] == "negative"
    assert rows == [{"segment_id": "s1", "stance": "for", "author_kind": "unsure"},
                    {"segment_id": "s2", "stance": "neutral", "author_kind": "bot"}]  # skipped by the model: neutral
    assert pc.parse({"relevant": False, "presentation": "negative", "comments": []}, [], set())[0]["presentation"] == "neutral"


def test_label_store_export_and_api(conn, graph, sc_settings, tmp_path):
    ids = _seed(conn, graph, sc_settings)
    labeller = pc.Labeller(conn, FakeLLM(fail_on="@" + "x" * 99), clock=lambda: FIXED_NOW)
    est = labeller.estimate(PID, "Claudia Sheinbaum")
    assert (est["pending"], est["would_label"], est["comments"]) == (3, 3, 5) and est["input_tokens"] > 3 * pc.SYSTEM_TOKENS
    assert labeller.estimate(PID, "Claudia Sheinbaum", max_posts=1)["would_label"] == 1

    out = labeller.run(PID, "Claudia Sheinbaum", workers=2)
    assert (out["labelled"], out["failed"], out["not_relevant"], out["comments"]) == (3, 0, 1, 5)
    assert out["input_tokens"] == 300 and labeller.pending(PID) == []
    kinds = {r["author_kind"]: r["n"] for r in conn.execute(
        "SELECT author_kind, count(*) AS n FROM comment_stance GROUP BY 1").fetchall()}
    assert kinds == {"bot": 3, "human": 2}  # the same text under 3 posts is a likely bot, whatever the model said
    assert pc.Labeller(conn, FakeLLM(), clock=lambda: FIXED_NOW).run(PID, "x")["labelled"] == 0  # already labelled

    m.export_engagement(conn, "MX", tmp_path)
    info = m.export_labels(conn, "MX", tmp_path)
    assert (info["pairs"], info["not_relevant"], info["models"]) == (3, 1, ["fake-model"])
    app = FastAPI()
    app.include_router(build_router(lambda: tmp_path))
    d = TestClient(app).get(f"/api/metrics/MX/politicians/{PID}").json()
    assert d["presentation"]["total"] == 2 and d["presentation"]["index"] == 100.0  # the not-relevant post is left out
    assert d["feedback"]["humans"] == 2 and d["feedback"]["bots"] == 2 and d["sources"]["labels"]["pairs"] == 3
    first = next(v for v in d["videos"] if v["labelled"] and v["feedback"] and v["feedback"]["humans"] == 2)
    assert first["presentation_reason"] == "praises her programme" and first["captured"]
    assert sum(v["labelled"] for v in d["videos"]) == 2 and len(d["videos"]) == len(ids)


def test_failed_posts_stay_pending_and_imported_labels_win(conn, graph, sc_settings, tmp_path):
    _seed(conn, graph, sc_settings)
    labeller = pc.Labeller(conn, FakeLLM(fail_on="namesake"), clock=lambda: FIXED_NOW)
    out = labeller.run(PID, "Claudia Sheinbaum")
    assert (out["labelled"], out["failed"]) == (2, 1) and "model unavailable" in out["errors"][0]
    assert len(labeller.pending(PID)) == 1

    m.export_engagement(conn, "MX", tmp_path)
    m.export_labels(conn, "MX", tmp_path)
    post = m.read_jsonl(tmp_path / "MX" / m.LABELS_FILE)[0]
    imported = {"monitor_slug": "claudia", "politician_id": PID, "politician_name": "Claudia", "platform": post["platform"],
                "platform_id": post["platform_id"], "presentation": "negative", "presentation_reason": "reviewed",
                "own_account": False, "feedback": None}
    m._write_jsonl(tmp_path / "MX" / m.PERCEPTION_FILE, [imported])
    app = FastAPI()
    app.include_router(build_router(lambda: tmp_path))
    d = TestClient(app).get(f"/api/metrics/MX/politicians/{PID}").json()
    assert (d["presentation"]["negative"], d["presentation"]["positive"]) == (1, 1)


def test_dry_run_works_before_the_migration(conn, graph, sc_settings):
    _seed(conn, graph, sc_settings)
    conn.execute("DROP TABLE comment_stance; DROP TABLE post_presentation")
    labeller = pc.Labeller(conn, FakeLLM())
    assert labeller.estimate(PID, "Claudia Sheinbaum")["pending"] == 3
    assert m.export_labels(conn, "MX", sc_settings.raw_storage_dir)["pairs"] == 0
    assert pc.tracked_politicians(conn, "mx")[0]["politician_id"] == PID
