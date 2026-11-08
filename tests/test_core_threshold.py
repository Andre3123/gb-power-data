import math
from fractions import Fraction

import numpy as np
import pytest

from gb_power_data.core.threshold import (
    conformal_threshold,
    count_events,
    exceedances,
    min_windows,
    one_tail_rank,
    window_rate,
)

WEEK = 1008  # ten-minute windows in one week (example, user's choice)


def clean_scores(weeks, seed, phi=0.8):
    """Made-up clean scores. Neighbouring windows move together (phi)."""
    rng = np.random.default_rng(seed)
    n = weeks * WEEK
    noise = rng.normal(0.0, 1.0, n)
    scale = math.sqrt(1.0 - phi**2)
    x = np.empty(n)
    x[0] = noise[0]
    for t in range(1, n):
        x[t] = phi * x[t - 1] + scale * noise[t]
    return np.abs(x)


def test_count_events_by_hand():
    flags = np.array([0, 1, 1, 0, 0, 1, 0, 1], dtype=bool)
    assert count_events(flags, merge_gap=0) == 3
    assert count_events(flags, merge_gap=1) == 2
    assert count_events(flags, merge_gap=2) == 1


def test_count_events_with_no_alarm_is_zero():
    assert count_events(np.zeros(10, dtype=bool)) == 0


def test_window_rate_one_per_week():
    assert window_rate(1.0, WEEK) == pytest.approx(1.0 / WEEK)


@pytest.mark.parametrize(
    "args",
    [(0.0, WEEK), (1.0, 0.0), (1.0, 1.0), (2000.0, 1000.0), (float("nan"), WEEK)],
)
def test_bad_rates_are_refused(args):
    with pytest.raises(ValueError):
        window_rate(*args)


def test_min_windows_for_one_per_week_is_1007():
    assert min_windows(1.0 / WEEK) == 1007


@pytest.mark.parametrize(
    ("rate", "first_n"),
    [(Fraction(1, 20), 1), (Fraction(1, 100), 1), (Fraction(1, 1008), 1000)],
)
def test_rank_matches_exact_arithmetic(rate, first_n):
    for n in range(first_n, first_n + 400):
        exact = math.ceil((n + 1) * (1 - rate))
        assert one_tail_rank(n, float(rate)) == exact


def test_too_few_windows_is_refused():
    with pytest.raises(ValueError):
        conformal_threshold(clean_scores(1, 0)[:500], None, 1.0, WEEK)


def test_too_few_expected_events_is_refused():
    scores = clean_scores(2, 0)  # 2016 windows -> only about 2 events allowed
    with pytest.raises(ValueError):
        conformal_threshold(scores, None, 1.0, WEEK)


def test_nan_score_is_refused():
    scores = clean_scores(100, 0)
    scores[5] = np.nan
    with pytest.raises(ValueError):
        conformal_threshold(scores, None, 1.0, WEEK)


def test_condition_length_must_match():
    with pytest.raises(ValueError):
        conformal_threshold(clean_scores(100, 0), ["a"] * 10, 1.0, WEEK)


def test_calibration_events_within_target_and_below_window_threshold():
    result = conformal_threshold(clean_scores(100, 3), None, 1.0, WEEK)["all"]
    assert result.calibration_events <= result.target_events
    assert result.threshold <= result.window_threshold


def test_held_out_event_rate_is_near_one_per_week():
    result = conformal_threshold(clean_scores(200, 1), None, 1.0, WEEK)["all"]
    new = clean_scores(200, 2)
    events_per_week = count_events(new > result.threshold) / 200
    assert 0.7 <= events_per_week <= 1.3


def two_condition_series(weeks, seed):
    """Made-up clean scores. Condition 1 has three times the spread."""
    n = weeks * WEEK
    block = 144  # one day of ten-minute windows
    rng = np.random.default_rng(seed + 100)
    labels = np.repeat(rng.integers(0, 2, n // block), block)
    scores = clean_scores(weeks, seed) * np.where(labels == 1, 3.0, 1.0)
    return scores, labels


def test_each_condition_gets_its_own_threshold_near_target():
    scores, labels = two_condition_series(200, 11)
    result = conformal_threshold(scores, labels, 1.0, WEEK)
    assert result["1"].threshold > 2.0 * result["0"].threshold

    new_scores, new_labels = two_condition_series(200, 12)
    alarms = exceedances(new_scores, new_labels, result)
    rate = 1.0 / WEEK
    for label in ("0", "1"):
        in_condition = new_labels == int(label)
        expected = rate * int(in_condition.sum())
        events = count_events(alarms & in_condition)
        assert 0.7 <= events / expected <= 1.3


def test_unknown_condition_is_refused():
    scores, labels = two_condition_series(100, 21)
    result = conformal_threshold(scores, labels, 1.0, WEEK)
    with pytest.raises(ValueError):
        exceedances(np.array([1.0, 2.0]), np.array([0, 7]), result)
