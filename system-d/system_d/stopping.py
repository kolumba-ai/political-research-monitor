"""Stopping rule for claim polling (DESIGN.md §4). Pure functions, no I/O."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from scipy.stats import t as student_t

Status = Literal["polling", "converged", "decisive", "capped"]
TERMINAL_STATUSES = frozenset({"converged", "decisive", "capped"})
Z95 = 1.959963984540054
SHARE_TOLERANCE = 1e-3


@dataclass(frozen=True)
class Stratum:
    id: str
    share: float


@dataclass(frozen=True)
class Response:
    stratum_id: str
    completed: bool
    believes_claim: bool | None = None
    sentiment: float | None = None


@dataclass(frozen=True)
class Config:
    seed_batch: int = 30
    next_batch: int = 20
    target_half_width: float = 0.07
    max_calls: int = 400
    min_per_stratum: int = 5
    max_attempts: int | None = None

    @property
    def attempt_cap(self) -> int:
        """Attempts (completed or not) after which a claim is capped; default 2 × max_calls."""
        return self.max_attempts if self.max_attempts is not None else 2 * self.max_calls


@dataclass(frozen=True)
class Stats:
    n_completed: int
    n_attempted: int
    completed_by_stratum: dict[str, int]
    p_believe: float | None
    ci_low: float | None
    ci_high: float | None
    n_effective: float | None
    sentiment_mean: float | None
    sentiment_ci_low: float | None
    sentiment_ci_high: float | None
    social_sentiment: float | None


@dataclass(frozen=True)
class Decision:
    status: Status
    next_batch_allocation: dict[str, int]
    stats: Stats
    reason: str


def wilson_interval(p: float, n: float, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a proportion `p` observed on (effective) sample size `n`."""
    if n <= 0:
        raise ValueError("n must be positive")
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def kish_effective_n(weights: list[float]) -> float:
    return sum(weights) ** 2 / sum(w * w for w in weights)


def post_stratification_weights(stratum_ids: list[str], strata: list[Stratum]) -> list[float]:
    """Weight per response = stratum share / stratum sample share.

    Shares of strata with no responses are spread over the observed strata so weights sum to n.
    """
    shares = {s.id: s.share for s in strata}
    unknown = set(stratum_ids) - shares.keys()
    if unknown:
        raise ValueError(f"responses from unknown strata: {sorted(unknown)}")
    counts = Counter(stratum_ids)
    n = len(stratum_ids)
    observed_share = sum(shares[s] for s in counts)
    if observed_share <= 0:
        raise ValueError("responses only from strata with zero share")
    return [(shares[s] / observed_share) / (counts[s] / n) for s in stratum_ids]


def _t_interval(values: list[float], weights: list[float]) -> tuple[float, float | None, float | None]:
    total = sum(weights)
    mean = sum(w * v for w, v in zip(weights, values, strict=True)) / total
    n_eff = kish_effective_n(weights)
    if n_eff < 2:
        return mean, None, None
    var = sum(w * (v - mean) ** 2 for w, v in zip(weights, values, strict=True)) / total
    var *= n_eff / (n_eff - 1)
    half = float(student_t.ppf(0.975, df=n_eff - 1)) * math.sqrt(var / n_eff)
    return mean, max(-1.0, mean - half), min(1.0, mean + half)


def compute_stats(responses: list[Response], strata: list[Stratum], social_sentiment: float | None) -> Stats:
    """Post-stratified p_believe with a Wilson 95% interval, and sentiment with a 95% t-interval.

    `believes_claim is None` ("no sé") counts as not believing. Both intervals use Kish's effective n.
    """
    completed = [r for r in responses if r.completed]
    by_stratum = dict(Counter(r.stratum_id for r in completed))
    p = lo = hi = n_eff = None
    if completed:
        w = post_stratification_weights([r.stratum_id for r in completed], strata)
        y = [1.0 if r.believes_claim is True else 0.0 for r in completed]
        p = sum(wi * yi for wi, yi in zip(w, y, strict=True)) / sum(w)
        n_eff = kish_effective_n(w)
        lo, hi = wilson_interval(p, n_eff)
    rated = [r for r in completed if r.sentiment is not None]
    s_mean = s_lo = s_hi = None
    if rated:
        w = post_stratification_weights([r.stratum_id for r in rated], strata)
        s_mean, s_lo, s_hi = _t_interval([float(r.sentiment) for r in rated], w)
    return Stats(
        n_completed=len(completed),
        n_attempted=len(responses),
        completed_by_stratum=by_stratum,
        p_believe=p,
        ci_low=lo,
        ci_high=hi,
        n_effective=n_eff,
        sentiment_mean=s_mean,
        sentiment_ci_low=s_lo,
        sentiment_ci_high=s_hi,
        social_sentiment=social_sentiment,
    )


def _ordered(strata: list[Stratum]) -> list[Stratum]:
    return sorted(strata, key=lambda s: (-s.share, s.id))


def allocate_proportional(total: int, strata: list[Stratum]) -> dict[str, int]:
    """Split `total` across strata in proportion to share (largest remainder)."""
    live = [s for s in strata if s.share > 0]
    if total <= 0 or not live:
        return {}
    share_sum = sum(s.share for s in live)
    exact = {s.id: total * s.share / share_sum for s in live}
    alloc = {sid: math.floor(x) for sid, x in exact.items()}
    left = total - sum(alloc.values())
    for s in sorted(live, key=lambda s: (-(exact[s.id] - alloc[s.id]), -s.share, s.id))[:left]:
        alloc[s.id] += 1
    return {sid: k for sid, k in alloc.items() if k > 0}


def allocate_to_deficit(
    total: int, strata: list[Stratum], completed_by_stratum: dict[str, int], min_per_stratum: int
) -> dict[str, int]:
    """Give each of `total` slots to the stratum furthest below its quota.

    Quota after the batch = max(min_per_stratum, share × (completed + total)).
    """
    live = _ordered([s for s in strata if s.share > 0])
    if total <= 0 or not live:
        return {}
    share_sum = sum(s.share for s in live)
    final_n = sum(completed_by_stratum.get(s.id, 0) for s in live) + total
    quota = {s.id: max(float(min_per_stratum), s.share / share_sum * final_n) for s in live}
    have = {s.id: completed_by_stratum.get(s.id, 0) for s in live}
    alloc: dict[str, int] = {}
    for _ in range(total):
        best = max(live, key=lambda s: quota[s.id] - have[s.id])
        have[best.id] += 1
        alloc[best.id] = alloc.get(best.id, 0) + 1
    return alloc


def decide(responses: list[Response], strata: list[Stratum], social_sentiment: float | None, cfg: Config) -> Decision:
    """Returns Decision(status, next_batch_allocation: dict[stratum_id, int], stats)."""
    if not any(s.share > 0 for s in strata):
        raise ValueError("no strata with positive share")
    share_sum = sum(s.share for s in strata)
    if abs(share_sum - 1.0) > SHARE_TOLERANCE:
        raise ValueError(f"strata shares sum to {share_sum}, expected 1.0")

    stats = compute_stats(responses, strata, social_sentiment)
    n = stats.n_completed

    if n >= cfg.max_calls:
        return Decision("capped", {}, stats, f"n_completed {n} reached MAX_CALLS {cfg.max_calls}")
    if stats.n_attempted >= cfg.attempt_cap:
        return Decision("capped", {}, stats, f"{stats.n_attempted} attempts reached the cap {cfg.attempt_cap}")

    if not responses:
        alloc = allocate_proportional(min(cfg.seed_batch, cfg.max_calls), strata)
        return Decision("polling", alloc, stats, "seed batch")

    short = [s.id for s in strata if s.share > 0 and stats.completed_by_stratum.get(s.id, 0) < cfg.min_per_stratum]
    if not short:
        if (
            social_sentiment is not None
            and stats.sentiment_ci_low is not None
            and not (stats.sentiment_ci_low <= social_sentiment <= stats.sentiment_ci_high)
        ):
            return Decision("decisive", {}, stats, "sentiment CI excludes social sentiment")
        if stats.ci_low is not None and (stats.ci_high - stats.ci_low) / 2 <= cfg.target_half_width:
            return Decision("converged", {}, stats, "p_believe CI half-width within target")

    room = min(cfg.next_batch, cfg.max_calls - n, cfg.attempt_cap - stats.n_attempted)
    alloc = allocate_to_deficit(room, strata, stats.completed_by_stratum, cfg.min_per_stratum)
    reason = f"{len(short)} strata below MIN_PER_STRATUM" if short else "CI not yet within target"
    return Decision("polling", alloc, stats, reason)
