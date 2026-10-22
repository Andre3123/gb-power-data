"""A scope gate for any agent: are today's inputs like the ones its ranges were learned on?

The customer chooses the features (prices, wind forecast, state of charge, temperature...).
Calibra learns a normal band per feature from the agent's own past forecasts, the 1st to 99th
percentile, and flags a new forecast whose inputs fall outside, are missing, or were never seen.
This is the applicability-domain idea (OECD QSAR principle 3), applied to agent forecasts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FeatureGate:
    low: dict[str, float]
    high: dict[str, float]
    rows: int  # past forecasts the bands were learned from

    @classmethod
    def learn(cls, history: list[dict[str, float]], tail: float = 0.01, min_rows: int = 20):
        """Bands from past inputs; a feature with fewer than min_rows values gets no band."""
        values: dict[str, list[float]] = {}
        for feats in history:
            for name, v in feats.items():
                if v is not None and math.isfinite(v):
                    values.setdefault(name, []).append(float(v))
        low, high = {}, {}
        for name, vs in values.items():
            if len(vs) >= min_rows:
                low[name] = float(np.quantile(vs, tail))
                high[name] = float(np.quantile(vs, 1 - tail))
        return cls(low, high, len(history))

    def flags(self, features: dict[str, float]) -> list[str]:
        """Reasons the inputs are out of scope; an empty list means in scope."""
        out = []
        for name in sorted(self.low):
            if name not in features or not math.isfinite(features[name]):
                out.append(f"{name} missing (learned on {self.rows} past forecasts)")
            elif features[name] < self.low[name]:
                out.append(f"{name} {features[name]:g} below learned {self.low[name]:g}")
            elif features[name] > self.high[name]:
                out.append(f"{name} {features[name]:g} above learned {self.high[name]:g}")
        for name in sorted(set(features) - set(self.low)):
            out.append(f"{name} has too little history to check")
        return out

    def bands(self) -> dict[str, list[float]]:
        return {n: [round(self.low[n], 6), round(self.high[n], 6)] for n in sorted(self.low)}
