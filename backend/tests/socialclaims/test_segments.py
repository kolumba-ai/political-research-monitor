from socialclaims import accounts
from socialclaims.capture import Capturer
from socialclaims.segments import PROCESSOR_VERSION, assign_roles, build_segments, process_politician

from .conftest import FIXED_NOW, FakeOriane, make_result

PID = "person:MX:claudia-sheinbaum-pardo"
NAMES = ["claudia sheinbaum"]
ACC = [{"account_id": 7, "platform": "tiktok", "handle": "claudiasheinbaum", "oriane_profile_id": "prf_own"}]


def video(**kw):
    media = {"type": "video", "transcriptLanguage": "es",
             "transcriptChunks": [{"startSeconds": "4.0", "endSeconds": "9.5", "text": "Bajó el desempleo"},
                                  {"startSeconds": "0.5", "endSeconds": "3.9", "text": "Buenos días"}],
             "frames": [{"timestampSeconds": "0.2", "onScreenTextRegions": [{"text": "EN VIVO"}]},
                        {"timestampSeconds": "1.2", "onScreenTextRegions": [{"text": "EN VIVO"}, {"text": "Mañanera"}]},
                        {"timestampSeconds": "2.2", "onScreenTextRegions": []}]}
    return make_result(1, media=[media], popularComments=[{"content": "bien", "publishedAt": "2026-11-11T00:00:00Z"}],
                       **kw)


def test_segments_are_time_coded_and_frames_merged():
    segs = build_segments(video())
    assert [s["source"] for s in segs] == ["speech", "speech", "onscreen", "onscreen", "caption", "comment"]
    assert (segs[0]["text"], segs[0]["start_s"]) == ("Buenos días", 0.5)
    vivo = next(s for s in segs if s["text"] == "EN VIVO")
    assert (vivo["start_s"], vivo["end_s"]) == (0.2, 1.2)
    assert segs[4]["text"] == "Claudia Sheinbaum habla de Morena #cdmx" and segs[5]["commented_at"] is not None


def test_mention_is_never_speaker():
    post = video(caption="Claudia Sheinbaum dijo que bajó el desempleo")
    roles = {r["role"]: r for r in assign_roles(post, build_segments(post), PID, NAMES, ACC)}
    assert roles["mentioned"]["status"] == "verified" and roles["quoted"]["status"] == "probable"
    assert roles["speaker"]["status"] == "unknown" and "account_owner" not in roles


def test_own_account_with_speech_is_only_probable_speaker():
    post = video(profileId="prf_own", profileHandle="claudiasheinbaum")
    roles = {r["role"]: r for r in assign_roles(post, build_segments(post), PID, NAMES, ACC)}
    assert roles["account_owner"]["status"] == "verified"
    assert roles["speaker"]["status"] == "probable"
    other = video(profileId="prf_own", profileHandle="claudiasheinbaum", platform="instagram")
    assert {r["role"] for r in assign_roles(other, build_segments(other), PID, NAMES, ACC)} == {"mentioned", "speaker"}


def test_process_stores_segments_roles_review_and_audio_groups(conn, graph, sc_settings):
    accounts.approve(conn, PID, "tiktok", "claudiasheinbaum", "tester", ["https://www.gob.mx"], "prf_own")
    first = video(profileId="prf_own", profileHandle="claudiasheinbaum", audioPlatformId="a1", audioType="original",
                  published="2026-11-09T12:00:00Z")
    repost = {**video(audioPlatformId="a1", audioType="original"), "id": "cnt_" + "2" * 28}
    bare = make_result(3, media=[])
    fake = FakeOriane([first, repost, bare])
    Capturer(conn, graph, fake.client(), sc_settings, clock=lambda: FIXED_NOW).capture(PID)
    out = process_politician(conn, graph, sc_settings.raw_storage_dir, PID, now=FIXED_NOW)
    assert (out["posts"], out["processed"], out["review"]) == (3, 3, 1)
    assert out["roles"]["speaker:probable"] == 1 and out["roles"]["speaker:unknown"] == 2
    pp = {r["oriane_id"]: r for r in conn.execute("SELECT * FROM post_processing").fetchall()}
    assert pp[repost["id"]]["canonical_oriane_id"] == first["id"]
    assert pp[bare["id"]]["low_information"] is True
    again = process_politician(conn, graph, sc_settings.raw_storage_dir, PID, now=FIXED_NOW)
    assert (again["processed"], again["skipped"], again["review"]) == (0, 3, 0)
    n = conn.execute("SELECT count(*) AS n FROM segment WHERE processor_version = %s", (PROCESSOR_VERSION,)).fetchone()
    assert n["n"] == out["segments"]
