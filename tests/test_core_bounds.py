import math
from fractions import Fraction

import numpy as np
import pytest

from gb_power_data.core.bounds import asymmetric_bounds, min_errors, tail_rank


def test_min_errors_for_90_percent_is_19():
    assert min_errors(0.90) == 19


def test_min_errors_for_80_percent_is_9():
    assert min_errors(0.80) == 9


def test_n_19_uses_smallest_and_largest():
    errors = np.arange(1, 20, dtype=float)  # 1..19
    lower, upper = asymmetric_bounds(errors, 0.90)
    assert lower == 1.0
    assert upper == 19.0


def test_n_39_drops_one_extreme_each_side():
    errors = np.arange(1, 40, dtype=float)  # 1..39
    rng = np.random.default_rng(1)
    rng.shuffle(errors)
    lower, upper = asymmetric_bounds(errors, 0.90)
    assert lower == 2.0  # 2nd smallest
    assert upper == 38.0  # 2nd largest


def test_n_59_drops_two_extremes_each_side():
    errors = np.arange(1, 60, dtype=float)
    lower, upper = asymmetric_bounds(errors, 0.90)
    assert lower == 3.0
    assert upper == 57.0


def test_too_few_errors_is_refused():
    with pytest.raises(ValueError):
        asymmetric_bounds(np.arange(18, dtype=float), 0.90)


def test_nan_in_errors_is_refused():
    errors = np.arange(30, dtype=float)
    errors[3] = np.nan
    with pytest.raises(ValueError):
        asymmetric_bounds(errors, 0.90)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5, float("nan"), True])
def test_invalid_coverage_is_refused(bad):
    with pytest.raises(ValueError):
        asymmetric_bounds(np.arange(50, dtype=float), bad)


@pytest.mark.parametrize("coverage", ["0.8", "0.9", "0.95", "0.99"])
def test_rank_matches_exact_arithmetic(coverage):
    # Independent check: exact fractions, no floating-point error.
    level = 1 - (1 - Fraction(coverage)) / 2
    for n in range(19, 300):
        exact = math.ceil((n + 1) * level)
        assert tail_rank(n, float(coverage)) == exact


def test_biased_errors_give_lopsided_range():
    rng = np.random.default_rng(0)
    errors = rng.gamma(2.0, 10.0, 200) - 15.0  # skewed, biased, GBP/MWh
    lower, upper = asymmetric_bounds(errors, 0.90)
    assert abs(lower) != pytest.approx(abs(upper), rel=0.05)


def test_coverage_on_new_errors_is_near_90_percent():
    rng = np.random.default_rng(0)

    def draw(m):
        return rng.gamma(2.0, 10.0, m) - 15.0

    hits = []
    for _ in range(2000):
        lower, upper = asymmetric_bounds(draw(39), 0.90)
        new = draw(1)[0]
        hits.append(lower <= new <= upper)
    assert 0.88 <= float(np.mean(hits)) <= 0.92
