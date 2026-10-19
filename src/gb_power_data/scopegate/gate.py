"""ScopeGate: flag inputs that are unlike the history the calibration learned from.

The calibration promises 90% only for days that resemble the learning days.
ScopeGate checks the inputs before the claim is made, using only data the agent
already has (day D-2 or earlier).
A flagged claim is still recorded for later analysis, but Calibra does not vouch
for its range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

FEATURES = ("spread", "max_price", "min_price", "volatility_7d")


def daily_stats(prices: pd.DataFrame) -> pd.DataFrame:
    """One row  per settlement day: max , min, spread, and the 7-day volatility  of the spread"""
    price = prices.groupby("settlement_date")["price"]
    stats = pd.DataFrame({"max_price": price.max(), "min_price": price.min()})
    stats = stats.reindex(pd.date_range(stats.index.min(), stats.index.max()).date)
    stats["spread"] = stats["max_price"] - stats["min_price"]
    stats["volatility_7d"] = stats["spread"].rolling(7, min_periods=5).std()
    return stats


@dataclass(frozen=True, eq=False)
class ScopeGate:
    stats: pd.DataFrame
    low: dict[str, float]
    high: dict[str, float]

    def flags(self, d: date) -> list[str]:
        """Reasons why the claim for day d is out of scope; an empty list means in scope."""
        source = d - timedelta(days=2)  # the newest day the agent can see
        if source not in self.stats.index or self.stats.loc[source, list(FEATURES)].isna().any():
            return ["no complete price history up to D-2"]
        row = self.stats.loc[source]
        reasons = []
        for name in FEATURES:
            if row[name] < self.low[name]:
                reasons.append(f"{name} {row[name]:.1f} below learned {self.low[name]:.1f}")
            elif row[name] > self.high[name]:
                reasons.append(f"{name} {row[name]:.1f} above learned {self.high[name]:.1f}")
        return reasons


def learn_gate(prices: pd.DataFrame, start: date, end: date, tail: float = 0.01) -> ScopeGate:
    """Learn the normal range of each feature on [start, end]: from the tail to 1 - tail."""
    stats = daily_stats(prices)
    learning = stats.loc[(stats.index >= start) & (stats.index <= end), list(FEATURES)]
    return ScopeGate(
        stats=stats,
        low=learning.quantile(tail).to_dict(),
        high=learning.quantile(1 - tail).to_dict(),
    )
