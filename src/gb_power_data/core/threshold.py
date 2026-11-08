"""One-sided alarm thresholds at a false-alarm rate the operator chooses.

Method: one-tail split conformal quantile (Angelopoulos and Bates,
arXiv 2107.07511), then lowered using the count of alarm EVENTS on clean
calibration data (runs declustering).

A score is one number per time window. Bigger means less healthy.
An alarm is: score > threshold.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np


def window_rate(alarms_per_unit: float, windows_per_unit: float) -> float:
    """Return the chance one clean window may alarm.

    Example: 1 alarm per week and 1008 windows per week gives 1 / 1008.
    """
    a = float(alarms_per_unit)
    w = float(windows_per_unit)
    if not (math.isfinite(a) and math.isfinite(w)) or a <= 0.0 or w <= 0.0:
        raise ValueError("alarms_per_unit and windows_per_unit must be positive")
    rate = a / w
    if not 0.0 < rate < 1.0:
        raise ValueError(f"alarm rate per window must be below 1, got {rate}")
    return rate


def one_tail_rank(n: int, miss_rate: float) -> int:
    """Return k = ceil((n + 1) * (1 - miss_rate)).

    Rounded to 9 decimals first, for the same floating-point reason as
    in core/bounds.py.
    """
    return math.ceil(round((n + 1) * (1.0 - miss_rate), 9))


def min_windows(miss_rate: float) -> int:
    """Smallest n for which the k-th smallest score exists (k <= n)."""
    n = max(1, math.ceil(round((1.0 - miss_rate) / miss_rate, 9)))
    while one_tail_rank(n, miss_rate) > n:
        n += 1
    return n


def count_events(exceed: np.ndarray, merge_gap: int = 0) -> int:
    """Count alarm events, not alarm windows.

    Two exceeding windows belong to one event when at most `merge_gap`
    quiet windows sit between them. merge_gap = 0 means only directly
    neighbouring windows are merged.
    """
    flags = np.asarray(exceed, dtype=bool)
    if flags.ndim != 1:
        raise ValueError("exceed must be a 1-D list of True/False")
    if merge_gap < 0 or int(merge_gap) != merge_gap:
        raise ValueError("merge_gap must be a whole number, 0 or more")
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return 0
    return 1 + int(np.sum(np.diff(idx) - 1 > merge_gap))


@dataclass(frozen=True)
class ThresholdResult:
    """Alarm level for one operating condition."""

    condition: str
    threshold: float  # alarm if score > threshold (same units as the score)
    window_threshold: float  # safe upper bound from the one-tail rank
    n_windows: int  # clean calibration windows in this condition
    target_events: float  # events the chosen rate allows on this data
    calibration_events: int  # events actually counted at `threshold`
    scan_capped: bool  # True if the scan stopped at max_candidates


def _labels(conditions: Sequence[object] | np.ndarray | None, size: int) -> np.ndarray:
    if conditions is None:
        return np.full(size, "all")
    labels = np.asarray(conditions).astype(str)
    if labels.shape != (size,):
        raise ValueError("conditions must have one label per score")
    return labels


def conformal_threshold(
    scores: Sequence[float] | np.ndarray,
    conditions: Sequence[object] | np.ndarray | None,
    alarms_per_unit: float,
    windows_per_unit: float,
    merge_gap: int = 0,
    min_events: int = 20,
    max_candidates: int = 500,
) -> dict[str, ThresholdResult]:
    """Return one ThresholdResult per operating condition.

    scores: clean calibration scores in time order, one per window.
    conditions: one label per window (for example a wind-speed bin), or None.
    """
    values = np.asarray(scores, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("scores must be a 1-D list of finite numbers")
    if min_events < 1 or max_candidates < 1:
        raise ValueError("min_events and max_candidates must be 1 or more")
    labels = _labels(conditions, values.size)
    rate = window_rate(alarms_per_unit, windows_per_unit)
    needed = min_windows(rate)

    results: dict[str, ThresholdResult] = {}
    for label in np.unique(labels):
        mask = labels == label
        own = values[mask]
        n = int(own.size)
        if n < needed:
            raise ValueError(f"condition {label!r}: need at least {needed} windows, got {n}")
        target = rate * n
        if target < min_events:
            raise ValueError(
                f"condition {label!r}: the chosen rate allows only "
                f"{target:.1f} events on this data, need at least {min_events}"
            )

        k = one_tail_rank(n, rate)
        window_threshold = float(np.sort(own)[k - 1])

        all_candidates = np.unique(own[own <= window_threshold])[::-1]
        candidates = all_candidates[:max_candidates]

        best = window_threshold
        best_events = count_events(mask & (values > best), merge_gap)
        broke_target = False
        for tau in candidates:
            events = count_events(mask & (values > tau), merge_gap)
            if events > target:
                broke_target = True
                break
            best, best_events = float(tau), events

        results[str(label)] = ThresholdResult(
            condition=str(label),
            threshold=best,
            window_threshold=window_threshold,
            n_windows=n,
            target_events=target,
            calibration_events=best_events,
            scan_capped=(not broke_target) and all_candidates.size > candidates.size,
        )
    return results


def exceedances(
    scores: Sequence[float] | np.ndarray,
    conditions: Sequence[object] | np.ndarray | None,
    thresholds: Mapping[str, ThresholdResult],
) -> np.ndarray:
    """Return True where a window alarms. Refuses a condition never calibrated."""
    values = np.asarray(scores, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("scores must be a 1-D list of finite numbers")
    labels = _labels(conditions, values.size)
    limit = np.empty(values.size)
    for label in np.unique(labels):
        if str(label) not in thresholds:
            raise ValueError(f"no threshold for condition {label!r}")
        limit[labels == label] = thresholds[str(label)].threshold
    return values > limit
