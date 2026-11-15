from datetime import date

from polgraph.eligibility import (AssessmentView, ContactDim, ContactPolicy, DemocracyPolicy, Gate,
                                  aggregate_contact_status, evaluate_contact, evaluate_democracy, overall_eligibility)

POLICY = DemocracyPolicy(accepted={"freedom_house": {"free"}, "vdem": {"liberal_democracy", "electoral_democracy"},
                                   "eiu": {"full_democracy", "flawed_democracy"}}, min_sources=2, max_age_years=3)
TODAY = date(2026, 11, 14)


def A(index, label, year=2026, verified=True, grey=False):
    return AssessmentView(index, label, year, verified, grey)


def test_all_sources_agree_democratic_passes():
    r = evaluate_democracy([A("freedom_house", "free"), A("vdem", "liberal_democracy")], POLICY, today=TODAY)
    assert r.status == Gate.passed and not r.disagreement


def test_all_sources_agree_not_democratic_fails():
    r = evaluate_democracy([A("freedom_house", "not_free"), A("vdem", "closed_autocracy")], POLICY, today=TODAY)
    assert r.status == Gate.failed


def test_disagreement_requires_review():
    r = evaluate_democracy([A("freedom_house", "partly_free"), A("vdem", "electoral_democracy")], POLICY, today=TODAY)
    assert r.status == Gate.needs_review and r.disagreement


def test_unverified_and_stale_assessments_are_ignored():
    r = evaluate_democracy([A("freedom_house", "free", verified=False), A("vdem", "liberal_democracy", year=2015)],
                           POLICY, today=TODAY)
    assert r.status == Gate.needs_review
    assert any("not confirmed" in x for x in r.reasons) and any("older" in x for x in r.reasons)


def test_insufficient_sources_requires_review():
    r = evaluate_democracy([A("freedom_house", "free")], POLICY, today=TODAY)
    assert r.status == Gate.needs_review


def test_grey_zone_requires_review_even_when_agreeing():
    r = evaluate_democracy([A("freedom_house", "free"), A("vdem", "electoral_democracy", grey=True)], POLICY, today=TODAY)
    assert r.status == Gate.needs_review


def test_human_review_resolves_only_ambiguous_cases():
    amb = [A("freedom_house", "partly_free"), A("vdem", "electoral_democracy")]
    assert evaluate_democracy(amb, POLICY, "approve", today=TODAY).status == Gate.passed
    assert evaluate_democracy(amb, POLICY, "reject", today=TODAY).status == Gate.failed
    clear_fail = [A("freedom_house", "not_free"), A("vdem", "closed_autocracy")]
    assert evaluate_democracy(clear_fail, POLICY, "approve", today=TODAY).status == Gate.failed


CPOL = ContactPolicy({"allowed"}, {"issue_communication", "fact_verification_request"}, {"organization"}, True)


def D(purpose, status, sender="organization", ok=True, recipient="elected_official"):
    return ContactDim(purpose, "email", sender, recipient, status, ok)


def test_unknown_never_passes_even_if_policy_lists_it():
    pol = ContactPolicy({"allowed", "unknown"}, CPOL.relevant_purposes, CPOL.relevant_senders, False)
    assert evaluate_contact("unknown", pol, "approved").status == Gate.failed


def test_contact_aggregate_takes_worst_relevant_status():
    st, _ = aggregate_contact_status([D("issue_communication", "allowed"), D("fact_verification_request", "restricted"),
                                      D("lobbying", "prohibited")], CPOL)
    assert st == "restricted"  # lobbying is not a configured purpose


def test_missing_purpose_or_evidence_makes_unknown():
    st, _ = aggregate_contact_status([D("issue_communication", "allowed")], CPOL)
    assert st == "unknown"
    st, _ = aggregate_contact_status([D("issue_communication", "allowed"), D("fact_verification_request", "allowed",
                                                                             ok=False)], CPOL)
    assert st == "unknown"


def test_irrelevant_sender_ignored():
    st, _ = aggregate_contact_status([D("issue_communication", "prohibited", sender="foreign_national"),
                                      D("issue_communication", "allowed"), D("fact_verification_request", "allowed")], CPOL)
    assert st == "allowed"


def test_contact_requires_human_review():
    assert evaluate_contact("allowed", CPOL, None).status == Gate.needs_review
    assert evaluate_contact("allowed", CPOL, "approved").status == Gate.passed
    assert evaluate_contact("allowed", CPOL, "rejected").status == Gate.failed
    assert evaluate_contact("restricted", CPOL, "approved").status == Gate.failed
    assert evaluate_contact("prohibited", CPOL, "approved").status == Gate.failed


def test_overall_requires_both():
    from polgraph.eligibility import GateResult
    p, f = GateResult(Gate.passed), GateResult(Gate.needs_review)
    assert overall_eligibility(p, p) and not overall_eligibility(p, f) and not overall_eligibility(f, p)
