"""score one claim against it s resolution.lower internval  score  is better"""

from __future__ import annotations

from gb_power_data.ledger.models import Claim, Resolution


def check_pair(Claim: Claim, res: Resolution) -> None:
    if res.claim_id != Claim.claim_id:
        raise ValueError(f"resolution is for {res.claim_id}, not {Claim.claim_id}")
    if res.resolved_at < Claim.target_end:
        raise ValueError("resolved before the target period ended")


def is_covered(Claim: Claim, res: Resolution) -> bool:
    """did the truth land inside the claimed  range"""
    check_pair(Claim, res)
    return Claim.lower <= res.realised_value <= Claim.upper


def interval_score(Claim: Claim, res: Resolution) -> float:
    """Width fee plus a fine of 2/alpha times the miss distance (Gneiting & Raftery 2007)."""
    check_pair(Claim, res)
    alpha = 1 - Claim.coverage
    y = res.realised_value
    width = Claim.upper - Claim.lower
    below = max(Claim.lower - y, 0.0)
    above = max(y - Claim.upper, 0.0)
    return width + (2 / alpha) * (below + above)
