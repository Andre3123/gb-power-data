"""Asymmetric prediction bounds from signed forecast errors.

Method: split conformal prediction, one tail at a time, with the
(n + 1) finite-sample correction.
Reading: Angelopoulos & Bates, "A Gentle Introduction to Conformal
Prediction", arXiv 2107.07511.

Errors are signed: error = actual - forecast (for example in GBP/MWh).
The bounds are offsets. The range for a new forecast f is
[f + lower, f + upper].
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def _check_coverage(coverage: float) -> float:
    """Refuse a coverage that is not a number strictly between 0 and 1."""
    if isinstance(coverage, bool):
        raise ValueError("coverage must be a number between 0 and 1, not a bool")
    try:
        value = float(coverage)
    except (TypeError, ValueError) as exc:
        raise ValueError("coverage must be a number between 0 and 1") from exc
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise ValueError(f"coverage must be strictly between 0 and 1, got {coverage!r}")
    return value


def tail_rank(n: int, coverage: float) -> int:
    """Return k: the rank to use for one tail.

    Each tail may miss (1 - coverage) / 2 of the time, so one tail needs
    level = 1 - (1 - coverage) / 2. Then k = ceil((n + 1) * level).
    We round to 9 decimals first. Without it, a value that should be
    exactly 19.0 can come out as 19.000000000000004, and ceil gives 20.
    """
    value = _check_coverage(coverage)
    level = 1.0 - (1.0 - value) / 2.0
    return math.ceil(round((n + 1) * level, 9))


def min_errors(coverage: float) -> int:
    """Smallest number of past errors that gives a finite honest range.

    We need k <= n. For coverage 0.90 this is 19.
    """
    value = _check_coverage(coverage)
    level = 1.0 - (1.0 - value) / 2.0
    n = max(1, math.ceil(round(level / (1.0 - level), 9)))
    while tail_rank(n, value) > n:
        n += 1
    return n


def asymmetric_bounds(
    signed_errors: Sequence[float] | np.ndarray, coverage: float
) -> tuple[float, float]:
    """Return (lower, upper) offsets, in the units of the errors.

    upper = k-th smallest error. lower = k-th largest error.
    A new error lands between them at least `coverage` of the time,
    if it comes from the same source as the past errors.
    """
    value = _check_coverage(coverage)
    errors = np.asarray(signed_errors, dtype=float)
    if errors.ndim != 1:
        raise ValueError("signed_errors must be a 1-D list of numbers")
    if not np.all(np.isfinite(errors)):
        raise ValueError("signed_errors contains NaN or infinite values")

    n = int(errors.size)
    needed = min_errors(value)
    if n < needed:
        raise ValueError(f"need at least {needed} errors for coverage {value}, got {n}")

    k = tail_rank(n, value)
    ordered = np.sort(errors)
    upper = float(ordered[k - 1])  # k-th smallest
    lower = float(ordered[n - k])  # k-th largest
    return lower, upper
