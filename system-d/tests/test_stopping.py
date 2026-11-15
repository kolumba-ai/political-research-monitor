import pytest

from system_d.stopping import (
    Config,
    Response,
    Stratum,
    allocate_proportional,
    allocate_to_deficit,
    compute_stats,
    decide,
    kish_effective_n,
    post_stratification_weights,
    wilson_interval,
)

TWO_STRATA = [Stratum("A", 0.5), Stratum("B", 0.5)]


def completed(stratum: str, believes: bool | None = True, sentiment: float | None = 0.2) -> Response:
    return Response(stratum, True, believes, sentiment)


def test_wilson_known_value_and_bounds():
    low, high = wilson_interval(0.5, 100)
    assert low == pytest.approx(0.4038, abs=0.0001)
    assert high == pytest.approx(0.5962, abs=0.0001)
    assert wilson_interval(0.0, 100)[0] == pytest.approx(0, abs=1e-15)
    assert wilson_interval(1.0, 100)[1] == pytest.approx(1)
    with pytest.raises(ValueError, match="n must be positive"):
        wilson_interval(0.5, 0)


def test_post_stratification_weights_for_proportional_sample_are_one():
    assert post_stratification_weights(["A", "A", "B", "B"], TWO_STRATA) == [1.0] * 4


def test_post_stratification_redistributes_missing_strata_share():
    strata = [Stratum("A", 0.25), Stratum("B", 0.25), Stratum("C", 0.5)]
    weights = post_stratification_weights(["A", "A", "B"], strata)
    assert weights == pytest.approx([0.75, 0.75, 1.5])
    assert sum(weights) == pytest.approx(3)


def test_post_stratification_rejects_unknown_and_zero_share_only_samples():
    with pytest.raises(ValueError, match="unknown strata"):
        post_stratification_weights(["missing"], TWO_STRATA)
    with pytest.raises(ValueError, match="zero share"):
        post_stratification_weights(["zero"], [Stratum("live", 1), Stratum("zero", 0)])


def test_kish_effective_sample_size():
    assert kish_effective_n([1, 1, 1]) == 3
    assert kish_effective_n([2, 1]) == pytest.approx(1.8)


def test_compute_stats_counts_no_se_as_not_believing_and_attempts():
    stats = compute_stats(
        [completed("A", None, None), Response("B", False, True, 0.5)],
        TWO_STRATA,
        None,
    )
    assert stats.n_completed == 1
    assert stats.n_attempted == 2
    assert stats.p_believe == 0
    assert stats.sentiment_mean is None
    assert stats.sentiment_ci_low is None
    assert stats.n_effective == 1


def test_compute_stats_empty_and_t_interval_edge_cases():
    empty = compute_stats([], TWO_STRATA, 0.1)
    assert empty.n_completed == empty.n_attempted == 0
    assert empty.p_believe is None
    assert empty.sentiment_mean is None
    one = compute_stats([completed("A", True, 0.4)], TWO_STRATA, None)
    assert one.sentiment_mean == pytest.approx(0.4)
    assert one.sentiment_ci_low is None
    assert one.sentiment_ci_high is None
    constant = compute_stats([completed("A", True, 0.4), completed("A", False, 0.4)], TWO_STRATA, None)
    assert constant.sentiment_mean == pytest.approx(0.4)
    assert constant.sentiment_ci_low == pytest.approx(0.4)
    assert constant.sentiment_ci_high == pytest.approx(0.4)


def test_allocate_proportionally_uses_deterministic_largest_remainder():
    assert allocate_proportional(5, TWO_STRATA) == {"A": 3, "B": 2}
    assert allocate_proportional(0, TWO_STRATA) == {}
    assert allocate_proportional(4, [Stratum("zero", 0)]) == {}


def test_allocate_to_deficit_prioritizes_lowest_quota():
    strata = [Stratum("A", 0.5), Stratum("B", 0.3), Stratum("C", 0.2)]
    allocation = allocate_to_deficit(4, strata, {"A": 10, "B": 0, "C": 9}, min_per_stratum=0)
    assert allocation == {"B": 4}


def test_allocate_to_deficit_breaks_ties_by_stratum_id():
    reversed_strata = [Stratum("B", 0.5), Stratum("A", 0.5)]
    assert allocate_to_deficit(1, reversed_strata, {}, min_per_stratum=0) == {"A": 1}
    assert allocate_to_deficit(0, reversed_strata, {}, min_per_stratum=0) == {}
    assert allocate_to_deficit(2, [Stratum("zero", 0)], {}, min_per_stratum=0) == {}


def test_decide_seed_allocation_respects_max_calls():
    decision = decide([], TWO_STRATA, None, Config(seed_batch=30))
    assert decision.status == "polling"
    assert sum(decision.next_batch_allocation.values()) == 30
    limited = decide([], TWO_STRATA, None, Config(seed_batch=30, max_calls=7))
    assert sum(limited.next_batch_allocation.values()) == 7


def test_decide_keeps_polling_until_each_positive_share_stratum_is_sampled():
    responses = [completed("A", i % 2 == 0, 0.4) for i in range(120)]
    decision = decide(
        responses,
        TWO_STRATA,
        0.4,
        Config(max_calls=400, min_per_stratum=5),
    )
    assert decision.status == "polling"
    assert decision.stats.completed_by_stratum == {"A": 120}


def test_decide_marks_sentiment_gap_decisive():
    responses = [completed(s, i % 2 == 0, 0.8) for s in ("A", "B") for i in range(10)]
    decision = decide(responses, TWO_STRATA, -0.8, Config(min_per_stratum=5))
    assert decision.status == "decisive"
    assert decision.reason == "sentiment CI excludes social sentiment"


def test_decide_does_not_mark_decisive_without_social_sentiment():
    responses = [completed(s, i % 2 == 0, 0.8) for s in ("A", "B") for i in range(10)]
    decision = decide(responses, TWO_STRATA, None, Config(min_per_stratum=5))
    assert decision.status == "polling"


def test_decide_marks_converged_when_wilson_interval_is_narrow_enough():
    responses = [completed("A" if i % 2 else "B", i % 2 == 0, 0.2) for i in range(400)]
    decision = decide(
        responses,
        TWO_STRATA,
        None,
        Config(max_calls=500, min_per_stratum=5, target_half_width=0.07),
    )
    assert decision.status == "converged"


def test_decisive_takes_precedence_over_converged():
    responses = [completed("A" if i % 2 else "B", i % 2 == 0, 1.0) for i in range(400)]
    decision = decide(
        responses,
        TWO_STRATA,
        -1.0,
        Config(max_calls=500, min_per_stratum=5, target_half_width=0.07),
    )
    assert decision.status == "decisive"


def test_decide_caps_at_completed_call_limit_even_if_strata_are_short():
    responses = [completed("A") for _ in range(10)]
    decision = decide(responses, TWO_STRATA, None, Config(max_calls=10, min_per_stratum=100))
    assert decision.status == "capped"
    assert decision.next_batch_allocation == {}


def test_decide_caps_all_refusals_at_attempt_limit():
    refusals = [Response("A", False) for _ in range(4)]
    decision = decide(refusals, TWO_STRATA, None, Config(max_attempts=4))
    assert decision.status == "capped"
    assert decision.stats.n_completed == 0
    assert decision.stats.n_attempted == 4


def test_decide_truncates_next_batch_to_remaining_completed_capacity():
    responses = [completed("A" if i % 2 else "B", i % 2 == 0) for i in range(8)]
    decision = decide(
        responses,
        TWO_STRATA,
        None,
        Config(max_calls=10, next_batch=20, min_per_stratum=0, max_attempts=100),
    )
    assert decision.status == "polling"
    assert sum(decision.next_batch_allocation.values()) == 2


def test_decide_requests_another_batch_after_incomplete_responses():
    decision = decide([Response("A", False)], TWO_STRATA, None, Config())
    assert decision.status == "polling"
    assert decision.next_batch_allocation


def test_decide_rejects_invalid_strata_shares():
    with pytest.raises(ValueError, match="no strata with positive share"):
        decide([], [Stratum("A", 0)], None, Config())
    with pytest.raises(ValueError, match="expected 1.0"):
        decide([], [Stratum("A", 0.6), Stratum("B", 0.3)], None, Config())
