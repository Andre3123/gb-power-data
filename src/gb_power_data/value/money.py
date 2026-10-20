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


def shortfall_by_scope(df: pd.DataFrame) -> pd.DataFrame:
    """Did ScopeGate warn before the money went missing? Floor promise split by in_scope.

    df: one row per claim with lower, realised and in_scope (ScopeGate's verdict, made from D-2
    data only, so it was known before the day).
    """
    d = df.assign(
        floor=df["lower"].clip(lower=0.0),
        shortfall=(df["lower"] - df["realised"]).clip(lower=0.0),
    )
    d["broken"] = d["shortfall"] > 1e-9
    out = d.groupby("in_scope").agg(
        days=("floor", "size"),
        promised_gbp=("floor", "sum"),
        broken_days=("broken", "sum"),
        shortfall_gbp=("shortfall", "sum"),
    )
    out["share_of_shortfall"] = out["shortfall_gbp"] / d["shortfall"].sum()
    return out.rename(index={True: "in scope", False: "flagged"})


def worst_days(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """The n days with the largest shortfall below the claim's lower bound."""
    d = df.assign(shortfall=(df["lower"] - df["realised"]).clip(lower=0.0))
    d["share_of_shortfall"] = d["shortfall"] / d["shortfall"].sum()
    cols = ["day", "lower", "realised", "shortfall", "share_of_shortfall", "in_scope"]
    return d.nlargest(n, "shortfall")[cols]