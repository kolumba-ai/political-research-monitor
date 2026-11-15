"""Country eligibility gates. Pure functions: no I/O, fully unit-tested.

Production enrichment requires BOTH gates to return `pass`. Anything ambiguous, unverified,
stale, unknown or awaiting human review does not pass. These are research/compliance signals,
not legal authorisation; nothing in this system initiates outreach.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable

from .config import Settings


class Gate(str, Enum):
    passed = "pass"
    failed = "fail"
    needs_review = "needs_review"


@dataclass
class AssessmentView:
    index: str                    # freedom_house | vdem | eiu
    label: str | None             # canonical classification
    year: int | None
    verified: bool                # label text found on the fetched evidence page
    grey_zone: bool = False


@dataclass
class GateResult:
    status: Gate
    reasons: list[str] = field(default_factory=list)
    disagreement: bool = False
    per_source: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"status": self.status.value, "reasons": self.reasons, "disagreement": self.disagreement,
                "per_source": self.per_source}


@dataclass
class DemocracyPolicy:
    accepted: dict[str, set[str]]
    min_sources: int = 2
    max_age_years: int = 3

    @classmethod
    def from_settings(cls, s: Settings) -> "DemocracyPolicy":
        return cls(
            accepted={
                "freedom_house": s.csv(s.democracy_accepted_freedom_house),
                "vdem": s.csv(s.democracy_accepted_vdem),
                "eiu": s.csv(s.democracy_accepted_eiu),
            },
            min_sources=s.democracy_min_sources,
            max_age_years=s.democracy_max_age_years,
        )


def evaluate_democracy(assessments: Iterable[AssessmentView], policy: DemocracyPolicy,
                       review_decision: str | None = None, today: date | None = None) -> GateResult:
    today = today or date.today()
    reasons: list[str] = []
    usable: dict[str, AssessmentView] = {}
    for a in assessments:
        if a.label is None:
            reasons.append(f"{a.index}: classification not recognised; ignored")
            continue
        if not a.verified:
            reasons.append(f"{a.index}: '{a.label}' not confirmed on evidence page; ignored")
            continue
        if a.year is None or today.year - a.year > policy.max_age_years:
            reasons.append(f"{a.index}: assessment year {a.year} is missing or older than {policy.max_age_years}y; ignored")
            continue
        prev = usable.get(a.index)
        if prev is None or (a.year or 0) > (prev.year or 0):
            usable[a.index] = a

    per_source = {}
    verdicts = []
    for idx, a in usable.items():
        ok = a.label in policy.accepted.get(idx, set())
        per_source[idx] = f"{a.label} ({a.year}) -> {'meets' if ok else 'does not meet'} policy"
        if a.grey_zone:
            reasons.append(f"{idx}: '{a.label}' is in the index's grey zone (uncertain category)")
        verdicts.append(ok)

    disagreement = len(set(verdicts)) > 1
    grey = any(a.grey_zone for a in usable.values())

    if len(usable) < policy.min_sources:
        status = Gate.needs_review
        reasons.append(f"only {len(usable)} usable verified source(s); policy requires {policy.min_sources}")
    elif disagreement:
        status = Gate.needs_review
        reasons.append("indices disagree on whether the country meets the policy")
    elif all(verdicts):
        status = Gate.needs_review if grey else Gate.passed
        if grey:
            reasons.append("all sources meet policy but at least one is a grey-zone classification")
    else:
        status = Gate.failed
        reasons.append("no source classifies the country within the accepted categories")

    if status == Gate.needs_review and review_decision in ("approve", "reject"):
        reasons.append(f"human review decision recorded: {review_decision}")
        status = Gate.passed if review_decision == "approve" else Gate.failed
    return GateResult(status, reasons, disagreement, per_source)


@dataclass
class ContactDim:
    purpose: str
    channel: str
    sender: str
    recipient: str
    status: str
    evidence_ok: bool  # an evidence URL was fetched and is non-empty


@dataclass
class ContactPolicy:
    accepted_statuses: set[str]
    relevant_purposes: set[str]
    relevant_senders: set[str]
    require_human_review: bool = True

    @classmethod
    def from_settings(cls, s: Settings) -> "ContactPolicy":
        return cls(s.csv(s.contact_accepted_statuses), s.csv(s.contact_relevant_purposes),
                   s.csv(s.contact_relevant_senders), s.require_human_review)


_SEVERITY = {"allowed": 0, "restricted": 1, "unknown": 2, "prohibited": 3}


def aggregate_contact_status(dims: Iterable[ContactDim], policy: ContactPolicy) -> tuple[str, list[str]]:
    """Worst status among dimensions relevant to this system's purpose. Unsupported evidence -> unknown."""
    reasons = []
    relevant = [
        d for d in dims
        if d.purpose in policy.relevant_purposes
        and (d.sender in policy.relevant_senders or d.sender == "any")
        and d.recipient in ("elected_official", "any")
    ]
    if not relevant:
        return "unknown", ["no researched rule covers the configured purpose/sender/recipient"]
    covered = {d.purpose for d in relevant}
    missing = policy.relevant_purposes - covered
    worst = "allowed"
    for d in relevant:
        st = d.status if d.status in _SEVERITY else "unknown"
        if not d.evidence_ok and st != "unknown":
            reasons.append(f"{d.purpose}/{d.channel}/{d.sender}: '{st}' lacks fetchable evidence -> unknown")
            st = "unknown"
        if _SEVERITY[st] > _SEVERITY[worst]:
            worst = st
    if missing:
        reasons.append(f"no rule found for purpose(s): {', '.join(sorted(missing))} -> unknown")
        if _SEVERITY["unknown"] > _SEVERITY[worst]:
            worst = "unknown"
    return worst, reasons


def evaluate_contact(status: str, policy: ContactPolicy, review_status: str | None,
                     extra_reasons: list[str] | None = None) -> GateResult:
    reasons = list(extra_reasons or [])
    if status == "unknown":
        reasons.append("contact legality unknown — unknown never passes the gate")
        return GateResult(Gate.failed, reasons, per_source={"aggregate": status})
    if status == "prohibited":
        reasons.append("researched rules prohibit the configured communication")
        return GateResult(Gate.failed, reasons, per_source={"aggregate": status})
    if status not in policy.accepted_statuses:
        reasons.append(f"status '{status}' not in accepted statuses {sorted(policy.accepted_statuses)}")
        return GateResult(Gate.failed, reasons, per_source={"aggregate": status})
    if policy.require_human_review and review_status != "approved":
        if review_status == "rejected":
            reasons.append("human reviewer rejected the contact-rule assessment")
            return GateResult(Gate.failed, reasons, per_source={"aggregate": status})
        reasons.append("awaiting human review of the legal assessment")
        return GateResult(Gate.needs_review, reasons, per_source={"aggregate": status})
    return GateResult(Gate.passed, reasons + [f"status '{status}' accepted"], per_source={"aggregate": status})


def overall_eligibility(democracy: GateResult, contact: GateResult) -> bool:
    return democracy.status == Gate.passed and contact.status == Gate.passed
