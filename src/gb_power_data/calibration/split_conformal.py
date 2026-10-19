"""Split conformal prediction: learn the range width from past mistakes.

Take the claims of a past period (the calibration set), measure how far each realised value
landed from the claimed point, and pick the distance that covers the promised share of them.
Vovk et al. (2005); Angelopoulos & Bates (2021), "A Gentle Introduction to Conformal Prediction".
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from gb_power_data.claims.dayahead import uk_midnight
from gb_power_data.ledger.store import Ledger


def conformal_half_width(residuals: np.ndarray, coverage: float) -> float:
    """Smallest distance that, with the finite-sample correction, covers `coverage` of them."""
    n = len(residuals)
    k = math.ceil((n + 1) * coverage)  # the (n + 1) is what makes the guarantee hold
    if n == 0 or k > n:
        need = math.ceil(round(coverage / (1 - coverage), 9))
        raise ValueError(f"need at least {need} past errors for {coverage:.0%} coverage")
    return float(np.sort(np.abs(residuals))[k - 1])


def mondrian_half_widths(by_class: dict[str, np.ndarray], coverage: float) -> dict[str, float]:
    """One half-width per day type, each learned only from that type's own past errors."""
    widths = {}
    for cls, residuals in sorted(by_class.items()):
        try:
            widths[cls] = conformal_half_width(residuals, coverage)
        except ValueError as err:
            raise ValueError(f"class '{cls}': {err}") from err
    return widths


def residuals_by_class(
    ledger: Ledger, agent_id: str, start: date, end: date
) -> dict[str, np.ndarray]:
    """realised - claimed point, grouped by the claim's day type (its reference class)."""
    rows = ledger.con.execute(
        """
        SELECT c.reference_class, r.realised_value - c.point
        FROM claims c
        JOIN resolutions r ON r.claim_id = c.claim_id
        WHERE c.agent_id = ?
          AND c.target_start >= ? AND c.target_start < ?
          AND r.resolution_version = (
              SELECT max(resolution_version) FROM resolutions WHERE claim_id = c.claim_id
          )
        """,
        [agent_id, _utc(start), _utc(end + timedelta(days=1))],
    ).fetchall()
    grouped: dict[str, list[float]] = {}
    for cls, residual in rows:
        grouped.setdefault(cls, []).append(residual)
    return {cls: np.array(values, dtype=float) for cls, values in grouped.items()}


def residuals_from_ledger(ledger: Ledger, agent_id: str, start: date, end: date) -> np.ndarray:
    """realised - claimed point, for one agent's resolved claims with targets in [start, end]."""
    by_class = residuals_by_class(ledger, agent_id, start, end)
    return np.concatenate(list(by_class.values())) if by_class else np.array([], dtype=float)


def _utc(d: date):
    """Start of a settlement day as the ledger stores it: UTC, without a timezone label."""
    return uk_midnight(d).replace(tzinfo=None)
