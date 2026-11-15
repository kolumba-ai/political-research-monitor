"""Social metrics: the engagement export, the LatamPoliticalMonitor label import, the indices, and the read API."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polgraph.metrics_api import MetricsExport, build_router
from socialclaims import metrics as m

CLARA = "person:MX:clara-marina-brugada-molina"
T = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


class Conn:
    """Stands in for the read-only System B connection."""

    def __init__(self, rows):
        self.rows, self.params = rows, None

    def execute(self, sql, params):
        assert "post_metrics" in sql and "INSERT" not in sql.upper()
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def _post(oid, platform_id, views, *, platform="tiktok", own=False, claims=0, likes=10, pid=CLARA):
    return {"politician_id": pid, "politician_name": "Clara Brugada Molina", "oriane_id": oid, "platform": platform,
            "platform_id": platform_id, "format": "video", "url": None, "uploader_handle": "clarabrugadam",
            "caption": "caption\u2028second line", "published_at": T, "views": views, "likes": likes, "comments": 4,
            "shares": 1, "interactions": None, "captured_at": T, "own_account": own, "claims": claims}


def _monitor(tmp_path):
    src = tmp_path / "monitor"
    src.mkdir()
    video = lambda vid, views, own=False: {"id": vid, "handle": "acc", "author": "Acc", "caption": "c", "views": views,  # noqa: E731
                                           "publishedAt": "2026-11-01T00:00:00.000Z", "likes": 2, "comments": 3,
                                           "shares": 1, "own": own}
    data = {"generatedAt": "2026-11-06T23:47:34+00:00", "range": {"after": "2026-10-07", "before": "2026-11-06"},
            "politicians": [
                {"slug": "clara-brugada", "name": "Clara Brugada", "role": "Jefa de Gobierno", "cats": ["x"],
                 "videos": [video("111", 900, own=True), video("222", 500), video("333", 100), video("444", 50)]},
                {"slug": "nobody-known", "name": "Nobody Known", "role": "r", "cats": ["x"], "videos": [video("222", 500)]},
            ]}
    labels = {"111|clara-brugada": {"p": "positive", "relevant": True, "why": "own announcement"},
              "222|clara-brugada": {"p": "negative", "relevant": True, "why": "criticism"},
              "333|clara-brugada": {"p": "positive", "relevant": True, "why": "praise"},
              "444|clara-brugada": {"p": "neutral", "relevant": False, "why": "not about her"},
              "222|nobody-known": {"p": "neutral", "relevant": True, "why": "passing mention"}}
    comments = {"111": {"comments": [{"cid": f"c{i}", "handle": "secret_handle", "text": "secret text"} for i in range(6)]}}
    comment_labels = {"c0": {"s": "pos", "b": "human"}, "c1": {"s": "pos", "b": "unsure"}, "c2": {"s": "neg", "b": "human"},
                      "c3": {"s": "neu", "b": "human"}, "c4": {"s": "pos", "b": "bot"}}
    for name, obj in (("morena_data.json", data), ("perception.json", labels), ("comments.json", comments),
                      ("comment_labels.json", comment_labels)):
        (src / name).write_text(json.dumps(obj), encoding="utf-8")
    return src


def test_indices():
    rows = [{"presentation": "positive", "own_account": True}, {"presentation": "positive", "own_account": False},
            {"presentation": "negative"}, {"presentation": "neutral"}, {"presentation": None}]
    p = m.presentation_summary(rows)
    assert (p["total"], p["index"], p["label"]) == (4, 25.0, "positive")
    assert (p["official"], p["favourable_third_party"], p["critical"]) == (1, 1, 1)
    f = m.feedback_counts({"for": 1, "against": 2, "neutral": 1, "bot_for": 5, "bot_against": 0, "bot_neutral": 0, "analyzed": 9})
    assert (f["humans"], f["bots"], f["index"], f["label"]) == (4, 5, -25.0, "negative")  # bots never move the index
    two = m.feedback_counts({"for": 2, "against": 0, "neutral": 0, "bot_for": 0, "bot_against": 0, "bot_neutral": 0, "analyzed": 2})
    assert two["index"] == 100.0 and two["label"] is None  # under MIN_HUMAN comments: no label for the video
    assert m.presentation_summary([]) is None and m.feedback_summary([{"feedback": None}]) is None
    assert m.reading("positive", True) == "official_communication"
    assert m.reading("positive", False) == "favourable_third_party" and m.reading("neutral", True) is None


def test_export_engagement_and_import_monitor(tmp_path):
    conn = Conn([_post("cnt_1", "111", 1000, own=True, claims=2), _post("cnt_2", "IGcode", None, platform="instagram")])
    info = m.export_engagement(conn, "mx", tmp_path)
    assert conn.params["pfx"] == "person:MX:%" and (info["posts"], info["politicians"]) == (2, 1)
    posts = m.read_jsonl(tmp_path / "MX" / m.POSTS_FILE)
    assert posts[0]["url"] == "https://www.tiktok.com/@clarabrugadam/video/111"
    assert posts[1]["url"] == "https://www.instagram.com/p/IGcode/" and "\u2028" in posts[0]["caption"]

    out = m.import_monitor(_monitor(tmp_path), "MX", tmp_path)
    assert (out["pairs"], out["dropped_not_relevant"], out["unmatched"]) == (4, 1, ["Nobody Known"])
    rows = m.read_jsonl(tmp_path / "MX" / m.PERCEPTION_FILE)
    assert {r["politician_id"] for r in rows} == {CLARA, None}
    first = next(r for r in rows if r["platform_id"] == "111")
    assert first["feedback"] == {"for": 2, "against": 1, "neutral": 1, "bot_for": 1, "bot_against": 0,
                                 "bot_neutral": 0, "analyzed": 5}
    text = (tmp_path / "MX" / m.PERCEPTION_FILE).read_text(encoding="utf-8")
    assert "secret_handle" not in text and "secret text" not in text  # commenters are never copied
    manifest = json.loads((tmp_path / "MX" / m.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert set(manifest) == {"engagement", "perception"}  # the second write keeps the first


def test_match_people_uses_crosswalk_then_exact_name():
    people = [{"slug": "pepe-chedraui", "name": "Pepe Chedraui"}, {"slug": "x", "name": "Marcelo Ebrard Casaubón"},
              {"slug": "y", "name": "Someone Else"}]
    got = m.match_people(people, "MX", {"person:MX:marcelo-ebrard-casaubon": "Marcelo Ebrard Casaubon"})
    assert got == {"pepe-chedraui": "person:MX:jose-chedraui-budib", "x": "person:MX:marcelo-ebrard-casaubon", "y": None}


def test_metrics_api(tmp_path):
    m.export_engagement(Conn([_post("cnt_1", "111", 1000, own=True, claims=2, likes=95),
                              _post("cnt_2", "IGcode", None, platform="instagram")]), "MX", tmp_path)
    m.import_monitor(_monitor(tmp_path), "MX", tmp_path)
    app = FastAPI()
    app.include_router(build_router(lambda: tmp_path, lambda pid: {"name": "Clara Marina Brugada Molina",
                                                                   "image_url": "https://img/x.jpg"} if pid == CLARA else None))
    api = TestClient(app)

    s = api.get("/api/metrics/mx/summary").json()
    assert s["available"] and [p["key"] for p in s["politicians"]] == [CLARA, "monitor:nobody-known"]
    clara, other = s["politicians"]
    assert clara["in_graph"] and clara["image_url"] and clara["name"] == "Clara Marina Brugada Molina"
    assert clara["engagement"]["posts"] == 2 and clara["engagement"]["views"] == 1000
    assert clara["engagement"]["engagement_rate"] == 10.0 and clara["engagement"]["own_posts"] == 1
    assert (clara["presentation"]["index"], clara["presentation"]["official"]) == (33.3, 1)
    assert clara["feedback"]["index"] == 25.0 and clara["feedback"]["bots"] == 1
    assert other["politician_id"] is None and other["engagement"] is None and not other["in_graph"]
    assert s["sources"]["perception"]["source"] == "LatamPoliticalMonitor" and "politicians" not in s["sources"]["perception"]

    d = api.get(f"/api/metrics/MX/politicians/{CLARA}").json()
    assert d["available"] and d["total"] == 4  # 2 captured posts + 3 labelled videos, one of them the same video
    top = d["videos"][0]
    assert top["platform_id"] == "111" and top["captured"] and top["labelled"] and top["claims"] == 2
    assert top["presentation"] == "positive" and top["reading"] == "official_communication"
    assert top["feedback"]["label"] == "positive" and top["views"] == 1000
    only = api.get(f"/api/metrics/MX/politicians/{CLARA}", params={"presentation": "negative"}).json()
    assert only["total"] == 1 and only["videos"][0]["captured"] is False and only["videos"][0]["reading"] == "critical_content"
    assert api.get("/api/metrics/MX/politicians/monitor:nobody-known").json()["total"] == 1
    assert api.get("/api/metrics/MX/politicians/person:MX:nobody").json()["available"] is False
    assert api.get(f"/api/metrics/MX/politicians/{CLARA}", params={"sort": "drop table"}).status_code == 422
    assert api.get("/api/metrics/PK/summary").json() == {"country": "PK", "available": False, "politicians": [],
                                                           "sources": {}, "method": s["method"]}


def test_metrics_for_a_claims_post(tmp_path):
    m.export_engagement(Conn([_post("cnt_1", "111", 1000, own=True)]), "MX", tmp_path)
    m.import_monitor(_monitor(tmp_path), "MX", tmp_path)
    export = MetricsExport(tmp_path)
    got = export.for_post("MX", CLARA, "cnt_1", None, None)
    assert got["engagement"]["views"] == 1000 and got["perception"]["presentation"] == "positive"
    assert export.for_post("MX", CLARA, None, "tiktok", "222")["perception"]["reading"] == "critical_content"
    assert export.for_post("MX", CLARA, "cnt_unknown", "tiktok", "999") is None
    assert export.for_post("PK", CLARA, "cnt_1", None, None) is None
