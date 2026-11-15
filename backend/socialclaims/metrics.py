"""Per-politician social metrics for the explorer: engagement, and the presentation and feedback indices.

Two export files sit next to the claims export, `<CLAIMS_EXPORT_DIR>/<ISO2>/`:

* `metrics_posts.jsonl`: one row per politician and captured post with its latest engagement counts, read from the
  System B Postgres (read-only, no API call).
* `metrics_perception.jsonl`: one row per politician and video with its presentation label and comment-stance counts,
  imported from a LatamPoliticalMonitor checkout. Commenters' handles and texts are not copied, only counts.
* `metrics_labels.jsonl`: the same labels for System B's own posts, written by `label-perception` to Postgres and
  exported with the engagement. Where both label a video for a politician, the imported (sample-reviewed) one is shown.

Presentation index: mean of +1 / 0 / -1 over politician-video pairs, times 100.
Feedback index: (for - against) / human comments, times 100; likely bots are counted apart.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .pastdev import post_url

POSTS_FILE, PERCEPTION_FILE, MANIFEST_FILE = "metrics_posts.jsonl", "metrics_perception.jsonl", "metrics_manifest.json"
LABELS_FILE = "metrics_labels.jsonl"
THRESHOLD = 20      # |index| at or above this reads as positive / negative
MIN_HUMAN = 3       # human comments a single video needs before its feedback gets a label
CAPTION_CHARS = 400

# LatamPoliticalMonitor slug -> System A person id, checked by hand against the knowledge graph. Slugs not listed
# here are matched by full name; anyone still unmatched is exported without a politician id.
MONITOR_PEOPLE = {
    "MX": {
        "alfonso-durazo": "person:MX:francisco-alfonso-durazo-montano",
        "aleida-alavez": "person:MX:aleida-alavez-ruiz",
        "janecarlo-lozano": "person:MX:ricardo-janecarlo-lozano-reynoso",
        "azucena-cisneros": "person:MX:azucena-cisneros-coss",
        "ismael-burgueno": "person:MX:ismael-burgueno-ruiz",
        "pepe-chedraui": "person:MX:jose-chedraui-budib",
        "javier-lopez-casarin": "person:MX:javier-joaquin-lopez-casarin",
        "omar-garcia-harfuch": "person:MX:omar-garcia-harfuch",
        "clara-brugada": "person:MX:clara-marina-brugada-molina",
        "marcelo-ebrard": "person:MX:marcelo-ebrard-casaubon",
        "adan-augusto-lopez": "person:MX:adan-augusto-lopez-hernandez",
        "ricardo-monreal": "person:MX:ricardo-monreal-avila",
        "rocio-nahle": "person:MX:norma-rocio-nahle-garcia",
        "gerardo-fernandez-norona": "person:MX:gerardo-fernandez-norona",
        "mario-delgado": "person:MX:mario-delgado-carrillo",
        "rosa-icela-rodriguez": "person:MX:rosa-icela-rodriguez-velazquez",
    },
}

ENGAGEMENT_SQL = """
WITH lm AS (
    SELECT DISTINCT ON (oriane_id) oriane_id, views, likes, shares, comments, interactions, captured_at
    FROM post_metrics ORDER BY oriane_id, captured_at DESC
), pp AS (
    SELECT DISTINCT politician_id, oriane_id FROM run_post WHERE relevance <> 'low' AND politician_id LIKE %(pfx)s
), own AS (
    SELECT DISTINCT person_id, oriane_id FROM post_person_role WHERE role = 'account_owner' AND status = 'verified'
), cl AS (
    SELECT politician_id, oriane_id, count(DISTINCT proposition_id) AS claims FROM distilled_claim GROUP BY 1, 2
), ref AS (
    SELECT DISTINCT ON (politician_id) politician_id, name FROM politician_ref ORDER BY politician_id, received_at DESC
)
SELECT pp.politician_id, ref.name AS politician_name, p.oriane_id, p.platform, p.platform_id, p.format, p.url,
       p.profile_handle AS uploader_handle, left(p.caption, %(chars)s) AS caption, p.published_at,
       lm.views, lm.likes, lm.comments, lm.shares, lm.interactions, lm.captured_at,
       (own.oriane_id IS NOT NULL) AS own_account, COALESCE(cl.claims, 0) AS claims
FROM pp JOIN post p ON p.oriane_id = pp.oriane_id
LEFT JOIN lm ON lm.oriane_id = pp.oriane_id
LEFT JOIN own ON own.person_id = pp.politician_id AND own.oriane_id = pp.oriane_id
LEFT JOIN cl ON cl.politician_id = pp.politician_id AND cl.oriane_id = pp.oriane_id
LEFT JOIN ref ON ref.politician_id = pp.politician_id
ORDER BY pp.politician_id, lm.views DESC NULLS LAST, p.oriane_id
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # split on "\n" only: captions may hold U+2028 and similar, which str.splitlines() treats as line ends
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _update_manifest(target: Path, key: str, value: dict) -> dict:
    path = target / MANIFEST_FILE
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    manifest[key] = value
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def export_engagement(conn, country: str, out_dir: Path | str) -> dict:
    """Write every captured post with its latest engagement counts, per politician. `conn` must be read-only."""
    country = country.upper()
    rows = []
    for r in conn.execute(ENGAGEMENT_SQL, {"pfx": f"person:{country}:%", "chars": CAPTION_CHARS}).fetchall():
        r = dict(r)
        r["url"] = r["url"] or post_url(r)
        r["published_at"], r["captured_at"] = _iso(r["published_at"]), _iso(r["captured_at"])
        rows.append(r)
    target = Path(out_dir) / country
    target.mkdir(parents=True, exist_ok=True)
    _write_jsonl(target / POSTS_FILE, rows)
    dates = [r["published_at"] for r in rows if r["published_at"]]
    info = {"source": "System B Postgres", "exported_at": _now(), "rows": len(rows),
            "posts": len({r["oriane_id"] for r in rows}), "politicians": len({r["politician_id"] for r in rows}),
            "first": min(dates, default=None), "last": max(dates, default=None),
            "captured_at": max((r["captured_at"] for r in rows if r["captured_at"]), default=None)}
    _update_manifest(target, "engagement", info)
    return info


LABELS_SQL = """
WITH x AS (
    SELECT DISTINCT ON (oriane_id, politician_id) * FROM post_presentation WHERE politician_id LIKE %(pfx)s
    ORDER BY oriane_id, politician_id, labelled_at DESC
), fb AS (
    SELECT oriane_id, politician_id, labeller_version, count(*) AS analyzed,
           count(*) FILTER (WHERE stance = 'for' AND author_kind <> 'bot') AS n_for,
           count(*) FILTER (WHERE stance = 'against' AND author_kind <> 'bot') AS n_against,
           count(*) FILTER (WHERE stance = 'neutral' AND author_kind <> 'bot') AS n_neutral,
           count(*) FILTER (WHERE stance = 'for' AND author_kind = 'bot') AS bot_for,
           count(*) FILTER (WHERE stance = 'against' AND author_kind = 'bot') AS bot_against,
           count(*) FILTER (WHERE stance = 'neutral' AND author_kind = 'bot') AS bot_neutral
    FROM comment_stance GROUP BY 1, 2, 3
)
SELECT x.politician_id, x.oriane_id, x.relevant, x.presentation, x.reason, x.model, x.labelled_at,
       p.platform, p.platform_id,
       EXISTS (SELECT 1 FROM post_person_role r WHERE r.oriane_id = x.oriane_id AND r.person_id = x.politician_id
               AND r.role = 'account_owner' AND r.status = 'verified') AS own_account,
       fb.analyzed, fb.n_for, fb.n_against, fb.n_neutral, fb.bot_for, fb.bot_against, fb.bot_neutral
FROM x JOIN post p ON p.oriane_id = x.oriane_id
LEFT JOIN fb ON fb.oriane_id = x.oriane_id AND fb.politician_id = x.politician_id
            AND fb.labeller_version = x.labeller_version
ORDER BY x.politician_id, x.oriane_id
"""


def export_labels(conn, country: str, out_dir: Path | str) -> dict:
    """Write `label-perception`'s labels per politician and post; empty before migration 007 or the first run."""
    country = country.upper()
    rows = []
    if conn.execute("SELECT to_regclass('post_presentation') AS t").fetchone()["t"] is not None:
        for r in conn.execute(LABELS_SQL, {"pfx": f"person:{country}:%"}).fetchall():
            feedback = None
            if r["analyzed"]:
                feedback = {"for": r["n_for"], "against": r["n_against"], "neutral": r["n_neutral"],
                            "bot_for": r["bot_for"], "bot_against": r["bot_against"],
                            "bot_neutral": r["bot_neutral"], "analyzed": r["analyzed"]}
            rows.append({"monitor_slug": None, "politician_id": r["politician_id"], "oriane_id": r["oriane_id"],
                         "platform": r["platform"], "platform_id": r["platform_id"] or r["oriane_id"],
                         "relevant": r["relevant"], "presentation": r["presentation"],
                         "presentation_reason": r["reason"], "own_account": bool(r["own_account"]),
                         "feedback": feedback, "model": r["model"], "labelled_at": _iso(r["labelled_at"])})
    target = Path(out_dir) / country
    target.mkdir(parents=True, exist_ok=True)
    _write_jsonl(target / LABELS_FILE, rows)
    info = {"source": "System B labelling", "exported_at": _now(), "pairs": len(rows),
            "politicians": len({r["politician_id"] for r in rows}),
            "not_relevant": sum(1 for r in rows if not r["relevant"]), "models": sorted({r["model"] for r in rows}),
            "labelled_at": max((r["labelled_at"] for r in rows), default=None)}
    _update_manifest(target, "labels", info)
    return info


def _fold(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if c.isalnum() or c == " ")


def match_people(politicians: list[dict], country: str, names: dict[str, str] | None = None) -> dict[str, str | None]:
    """Monitor slug -> System A id: the reviewed crosswalk first, then an exact full-name match in `names`."""
    known = MONITOR_PEOPLE.get(country.upper(), {})
    by_name = {_fold(n): pid for pid, n in (names or {}).items()}
    return {p["slug"]: known.get(p["slug"]) or by_name.get(_fold(p["name"])) for p in politicians}


def import_monitor(source: Path | str, country: str, out_dir: Path | str, names: dict[str, str] | None = None) -> dict:
    """Import a LatamPoliticalMonitor checkout (`morena_data.json` plus its label files) as perception rows."""
    source, country = Path(source), country.upper()

    def load(name: str, required: bool = False) -> dict:
        path = source / name
        if not path.exists():
            if required:
                raise FileNotFoundError(f"{name} not found in {source}")
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    data = load("morena_data.json", required=True)
    labels, comments, comment_labels = load("perception.json"), load("comments.json"), load("comment_labels.json")
    people = match_people(data["politicians"], country, names)

    feedback: dict[str, dict] = {}
    for vid, entry in comments.items():
        labelled = [comment_labels[c["cid"]] for c in entry.get("comments", []) if c.get("cid") in comment_labels]
        if not labelled:
            continue
        f = {k: 0 for k in ("for", "against", "neutral", "bot_for", "bot_against", "bot_neutral")}
        for lab in labelled:
            stance = {"pos": "for", "neg": "against"}.get(lab.get("s"), "neutral")
            f[f"bot_{stance}" if lab.get("b") == "bot" else stance] += 1
        feedback[vid] = {**f, "analyzed": len(labelled)}

    rows, dropped, unlabelled = [], 0, 0
    for p in data["politicians"]:
        for v in p["videos"]:
            lab = labels.get(f'{v["id"]}|{p["slug"]}')
            if lab and lab.get("relevant") is False:
                dropped += 1
                continue
            unlabelled += not lab
            rows.append({
                "monitor_slug": p["slug"], "politician_id": people[p["slug"]], "politician_name": p["name"],
                "platform": "tiktok", "platform_id": v["id"],
                "url": post_url({"platform": "tiktok", "platform_id": v["id"], "uploader_handle": v.get("handle")}),
                "uploader_handle": v.get("handle"), "uploader_name": v.get("author"),
                "caption": (v.get("caption") or "")[:CAPTION_CHARS], "published_at": v.get("publishedAt"),
                "views": v.get("views"), "likes": v.get("likes"), "comments": v.get("comments"),
                "shares": v.get("shares"), "own_account": bool(v.get("own")),
                "presentation": (lab or {}).get("p"), "presentation_reason": (lab or {}).get("why"),
                "feedback": feedback.get(v["id"]),
            })
    target = Path(out_dir) / country
    target.mkdir(parents=True, exist_ok=True)
    _write_jsonl(target / PERCEPTION_FILE, rows)
    info = {"source": "LatamPoliticalMonitor", "imported_at": _now(), "generated_at": data.get("generatedAt"),
            "range": data.get("range"), "platform": "tiktok", "pairs": len(rows),
            "videos": len({r["platform_id"] for r in rows}), "dropped_not_relevant": dropped, "unlabelled": unlabelled,
            "videos_with_feedback": len({r["platform_id"] for r in rows if r["feedback"]}),
            "politicians": [{"slug": p["slug"], "name": p["name"], "role": p.get("role"),
                             "categories": p.get("cats") or [], "politician_id": people[p["slug"]]}
                            for p in data["politicians"]],
            "unmatched": [p["name"] for p in data["politicians"] if not people[p["slug"]]]}
    _update_manifest(target, "perception", info)
    return {k: v for k, v in info.items() if k != "politicians"}


# --- indices ----------------------------------------------------------------------------------------------------

def index_label(score: float | None) -> str | None:
    if score is None:
        return None
    return "positive" if score >= THRESHOLD else "negative" if score <= -THRESHOLD else "neutral"


def presentation_summary(rows: list[dict]) -> dict | None:
    """Counts and index over labelled politician-video pairs, with the positive ones split by who posted them."""
    rows = [r for r in rows if r.get("presentation")]
    if not rows:
        return None
    n = {k: sum(1 for r in rows if r["presentation"] == k) for k in ("positive", "neutral", "negative")}
    index = (n["positive"] - n["negative"]) * 100 / len(rows)
    official = sum(1 for r in rows if r["presentation"] == "positive" and r.get("own_account"))
    return {**n, "total": len(rows), "index": round(index, 1), "label": index_label(index), "official": official,
            "favourable_third_party": n["positive"] - official, "critical": n["negative"]}


def feedback_counts(f: dict | None) -> dict | None:
    """One video's comment stance: humans only in the index; needs MIN_HUMAN human comments for a label."""
    if not f:
        return None
    humans = f["for"] + f["against"] + f["neutral"]
    index = (f["for"] - f["against"]) * 100 / humans if humans else None
    return {**f, "humans": humans, "bots": f["bot_for"] + f["bot_against"] + f["bot_neutral"],
            "index": None if index is None else round(index, 1),
            "label": index_label(index) if humans >= MIN_HUMAN else None}


def feedback_summary(rows: list[dict]) -> dict | None:
    """Human comments pooled across videos; a video counted for several politicians is pooled once per politician."""
    rows = [r for r in rows if r.get("feedback")]
    if not rows:
        return None
    total = {k: sum(r["feedback"][k] for r in rows)
             for k in ("for", "against", "neutral", "bot_for", "bot_against", "bot_neutral", "analyzed")}
    out = feedback_counts(total)
    return {**out, "label": index_label(out["index"]), "videos": len(rows)}


def reading(presentation: str | None, own_account: bool) -> str | None:
    if presentation == "positive":
        return "official_communication" if own_account else "favourable_third_party"
    return "critical_content" if presentation == "negative" else None
