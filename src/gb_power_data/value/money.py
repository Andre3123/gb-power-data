"""Turn claims and resolutions into pounds.

Two questions, each answered from the ledger:

1. Floor promise: an owner tells a lender (or an offtaker) "we will earn at least the claim's
   lower bound today". How much was promised, and how much went missing on the days the
   promise broke? That is the money an honest range protects, and a narrow one unlocks.
2. Capture rate: of the best revenue the battery could have earned with perfect knowledge of
   the day's prices, what share did the claimed plan really earn, after wear? The gap is the
   money a better forecast could add.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def floor_promise(df: pd.DataFrame) -> pd.DataFrame:
    """Per agent: the money promised as safe and the money missing when a promise broke.

    df has one row per resolved claim with columns agent, lower, realised (GBP).
    Promised: the lower bound, or nothing when the lower bound is negative (the claim then
    warns that the day may lose money). Broken: the day earned less than the claim's own lower
    bound; the shortfall is the gap. A loss the claim warned about is not a broken promise.
    """
    d = df.assign(floor=df["lower"].clip(lower=0.0))
    d["shortfall"] = (d["lower"] - d["realised"]).clip(lower=0.0)
    d["broken"] = d["shortfall"] > 1e-9
    out = d.groupby("agent", sort=False).agg(
        days=("floor", "size"),
        promised_gbp=("floor", "sum"),
        broken_days=("broken", "sum"),
        shortfall_gbp=("shortfall", "sum"),
        worst_day_gbp=("shortfall", "max"),
    )
    out["broken_share"] = out["broken_days"] / out["days"]
    out["shortfall_per_100_promised"] = 100 * out["shortfall_gbp"] / out["promised_gbp"]
    return out


def wear_cost(net_mw: np.ndarray, dt_h: float, gbp_per_mwh: float) -> float:
    """Wear of a plan: GBP per MWh discharged. A plan never charges and discharges at once,
    so the positive part of the net flow is the discharge."""
    return gbp_per_mwh * dt_h * float(np.clip(net_mw, 0.0, None).sum())


def capture(df: pd.DataFrame) -> pd.DataFrame:
    """Share of perfect-foresight net revenue the claimed plans earned, overall and by day type.

    df: one row per day with day_type, realised_net_gbp, perfect_net_gbp.
    A ratio of sums, not a mean of daily ratios: a day where the best possible revenue is near
    zero would otherwise give a huge or negative ratio and swamp the average.
    """

    def one(g: pd.DataFrame) -> pd.Series:
        return pd.Series(
            {
                "days": len(g),
                "earned_net_gbp": g["realised_net_gbp"].sum(),
                "possible_net_gbp": g["perfect_net_gbp"].sum(),
                "left_on_table_gbp": (g["perfect_net_gbp"] - g["realised_net_gbp"]).sum(),
            }
        )

    rows = {t: one(g) for t, g in df.groupby("day_type")}
    rows["all"] = one(df)
    out = pd.DataFrame(rows).T
    out["capture_rate"] = out["earned_net_gbp"] / out["possible_net_gbp"]
    out["days"] = out["days"].astype(int)
    return out