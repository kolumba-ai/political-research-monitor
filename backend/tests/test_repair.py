from polgraph import ids
from polgraph.evidence import EvidenceStatus, SupportingSource, Verification
from polgraph.graph.writer import GraphWriter
from polgraph.repair import repair_people
from polgraph.schemas import OfficeholderX


def _ver():
    return Verification(EvidenceStatus.official_single, [SupportingSource("https://gob.mx/x", None, "gob.mx", 1, "t")])


def test_repair_splits_wrongly_merged_people(store):
    w = GraphWriter(store, "test", "run")
    w.node("country:MX", "Country", name="Mexico")
    w.node("body:a", "GovernmentBody", name="Executive")
    gov = w.office("body:a", title_en="Governor of Aguascalientes", title_local=None, selection_method="direct_election",
                   is_elected=True)
    dep = w.office("body:a", title_en="Federal Deputy (list)", title_local=None, selection_method="direct_election",
                   is_elected=True)
    good = w.holding("MX", gov, OfficeholderX(full_name="María Teresa Jiménez Esquivel", party_acronym="PAN"), _ver(),
                     single_seat=True)
    # Simulate the old bug: attach a different person's holding to the governor's Person node.
    pid = good["person"]
    tid = ids.term_id(dep, pid, "2024-09-01")
    store.upsert_node(tid, "Term", {"name": "María Teresa Ealy Díaz — term from 2024-09-01", "person": pid})
    store.upsert_edge(dep, "HAS_TERM", tid, {})
    store.upsert_edge(tid, "TERM_HELD_BY", pid, {})
    store.upsert_edge(pid, "HOLDS_OFFICE", dep, {"current": True, "term_id": tid, "roster_name": "Ealy Díaz María Teresa"})
    store.upsert_node(pid, "Person", {"name": "María Teresa Jiménez Esquivel", "aliases": ["Ealy Díaz María Teresa"]})

    stats = repair_people(store, "MX")
    assert stats["persons_split"] == 1 and stats["holdings_moved"] == 1
    gov_holders = [p["name"] for _, p in store.edges(gov, "HOLDS_OFFICE", "in")]
    dep_holders = [p["name"] for _, p in store.edges(dep, "HOLDS_OFFICE", "in")]
    assert gov_holders == ["María Teresa Jiménez Esquivel"] and dep_holders == ["María Teresa Ealy Díaz"]
    assert store.get_node(pid)["aliases"] == []
    assert store.edges(pid, "MEMBER_OF_PARTY", "out")              # governor keeps her party
    assert repair_people(store, "MX")["persons_split"] == 0          # idempotent


def test_namesakes_in_different_legislatures_stay_separate(store):
    w = GraphWriter(store, "test", "run")
    w.node("body:kp", "GovernmentBody", name="KP Assembly")
    w.node("body:pb", "GovernmentBody", name="Punjab Assembly")
    kp = w.office("body:kp", title_en="Member (KP)", title_local=None, selection_method="direct_election",
                  is_elected=True, single_seat=False)
    pb = w.office("body:pb", title_en="Member (Punjab)", title_local=None, selection_method="direct_election",
                  is_elected=True, single_seat=False)
    a = w.holding("PK", kp, OfficeholderX(full_name="Muhammad Iqbal Khan"), _ver(), single_seat=False,
                  extra={"constituency_code": "PK-1"})
    b = w.holding("PK", pb, OfficeholderX(full_name="Muhammad Iqbal Khan"), _ver(), single_seat=False,
                  extra={"constituency_code": "PP-9"})
    assert a["person"] != b["person"]
    again = w.holding("PK", kp, OfficeholderX(full_name="Muhammad Iqbal Khan"), _ver(), single_seat=False,
                      extra={"constituency_code": "PK-1"})
    assert again["person"] == a["person"]                       # idempotent re-ingest


def test_repair_rebuilds_holding_lost_in_shared_edge(store):
    w = GraphWriter(store, "test", "run")
    w.node("body:c", "GovernmentBody", name="Chamber")
    dep = w.office("body:c", title_en="Deputy", title_local=None, selection_method="direct_election",
                   is_elected=True, single_seat=False)
    a = w.holding("MX", dep, OfficeholderX(full_name="Miguel Ángel Salim Alle"), _ver(), single_seat=False,
                  extra={"constituency_code": "GTO-DF11"})
    # Simulate an old merge: a second deputy's Term attached to the same person, sharing the one edge.
    tid = ids.term_id(dep, a["person"], "2024-09-02")
    store.upsert_node(tid, "Term", {"name": "Miguel Ángel Sánchez Rivera — term from 2024-09-02", "office": dep,
                                    "person": a["person"], "current": True, "constituency_code": "CIRC-2",
                                    "evidence_status": "official_single"})
    store.upsert_edge(dep, "HAS_TERM", tid, {})
    store.upsert_edge(tid, "TERM_HELD_BY", a["person"], {})
    repair_people(store, "MX")
    holders = {p["name"]: e for e, p in store.edges(dep, "HOLDS_OFFICE", "in")}
    assert set(holders) == {"Miguel Ángel Salim Alle", "Miguel Ángel Sánchez Rivera"}
    assert holders["Miguel Ángel Sánchez Rivera"]["constituency_code"] == "CIRC-2"
