"""The Calibra engine for any forecaster: record, range, gate, resolve, report.

For each forecast, Calibra uses only that agent's own outcomes that were known when the
forecast was made (target period ended at or before as_of). From them it learns:
- the range: split conformal on |realised - point|, per day type when that type has enough
  history (Mondrian), otherwise pooled over all types, otherwise none yet;
- the scope: a 1-99% band per input feature (FeatureGate).
Then it writes the forecast with that range to the hash-chained ledger, before the outcome.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from gb_power_data.calibra.gate import FeatureGate
from gb_power_data.calibra.schema import Forecast, Outcome
from gb_power_data.calibration.split_conformal import conformal_half_width
from gb_power_data.ledger.models import Claim
from gb_power_data.ledger.scoring import interval_score, is_covered
from gb_power_data.ledger.store import Ledger
from gb_power_data.value.money import floor_promise, shortfall_by_scope

SOURCE = "byof"  # marks claims that came in through this engine
RESOLUTION_RULE = "outcome supplied by the forecaster's own meter or settlement data"

LATEST = """
    SELECT * FROM resolutions
    QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY resolution_version DESC) = 1
"""


@dataclass(frozen=True)
class Calibration:
    """How Calibra would range a forecast made at `as_of`, from the history known then."""

    method: str  # mondrian | pooled | none
    half_width: float | None
    past_outcomes: int
    class_outcomes: int
    gate: FeatureGate
    note: str = ""


@dataclass
class Settings:
    coverage: float = 0.90
    min_per_class: int = 20  # below this, a day type borrows the pooled width
    gate_tail: float = 0.01
    gate_min_rows: int = 20
    code_version: str = "unknown"
    extra: dict = field(default_factory=dict)


def _db(dt: datetime) -> datetime:
    return pd.Timestamp(dt).tz_convert("UTC").tz_localize(None).to_pydatetime()


class Calibra:
    def __init__(self, ledger_path: str | Path, settings: Settings | None = None) -> None:
        self.ledger_path = Path(ledger_path)
        self.settings = settings or Settings()
        if not 0 < self.settings.coverage < 1:
            raise ValueError("coverage must be between 0 and 1")

    @contextmanager
    def _ledger(self, ledger: Ledger | None = None) -> Iterator[Ledger]:
        if ledger is not None:
            yield ledger
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        led = Ledger(self.ledger_path)
        try:
            yield led
        finally:
            led.close()

    # ---- learning from the agent's own past ------------------------------------------

    def history(self, agent_id: str, known_at: datetime, ledger: Ledger | None = None):
        """The agent's resolved forecasts whose target period ended by `known_at`."""
        q = f"""
            SELECT c.reference_class, c.point, c.context, r.realised_value
            FROM claims c JOIN ({LATEST}) r USING (claim_id)
            WHERE c.agent_id = ? AND c.target_end <= ?
            ORDER BY c.target_end
        """
        with self._ledger(ledger) as led:
            rows = led.con.execute(q, [agent_id, _db(known_at)]).fetchall()
        out = []
        for cls, point, ctx, realised in rows:
            context = json.loads(ctx)
            if context.get("source") == SOURCE:
                out.append((cls, realised - point, context.get("features", {})))
        return out

    def calibration(
        self,
        agent_id: str,
        as_of: datetime,
        day_type: str | None = None,
        ledger: Ledger | None = None,
    ) -> Calibration:
        s = self.settings
        hist = self.history(agent_id, as_of, ledger)
        gate = FeatureGate.learn([f for _, _, f in hist], s.gate_tail, s.gate_min_rows)
        pooled = np.array([r for _, r, _ in hist], dtype=float)
        own = np.array([r for c, r, _ in hist if day_type is not None and c == day_type])
        if day_type is not None and len(own) >= s.min_per_class:
            hw = conformal_half_width(own, s.coverage)
            return Calibration("mondrian", hw, len(pooled), len(own), gate)
        try:
            hw = conformal_half_width(pooled, s.coverage)
        except ValueError as e:
            note = f"no range yet: {e}; {len(pooled)} known so far"
            return Calibration("none", None, len(pooled), len(own), gate, note)
        note = (
            f"day type '{day_type}' has {len(own)} past outcomes (< {s.min_per_class}): "
            "pooled width used"
            if day_type is not None
            else ""
        )
        return Calibration("pooled", hw, len(pooled), len(own), gate, note)

    # ---- the three operations ----------------------------------------------------------

    def submit(self, f: Forecast, reason: str | None = None, ledger: Ledger | None = None) -> dict:
        """Record a forecast with Calibra's range. The same forecast twice returns the stored
        claim; the same forecast_id with different content is refused."""
        with self._ledger(ledger) as led:
            try:
                stored = led.get_claim(f.claim_id)
            except KeyError:
                stored = None
            if stored is not None:
                if stored.input_data_hash != f.input_hash():
                    raise ValueError(
                        f"{f.claim_id} was already recorded with different content; "
                        "a forecast is written once, so send a new forecast_id"
                    )
                return receipt(stored, new=False)
            cal = self.calibration(f.agent_id, f.as_of, f.day_type, led)
            flags = cal.gate.flags(f.features)
            half = cal.half_width or 0.0
            context = {
                "source": SOURCE,
                "calibration": {
                    "method": cal.method,
                    "half_width": cal.half_width,
                    "past_outcomes": cal.past_outcomes,
                    "class_outcomes": cal.class_outcomes,
                    "note": cal.note,
                },
                "agent_range": None if f.lower is None else [f.lower, f.upper],
                "features": f.features,
                "scope_flags": flags,
                "in_scope": not flags,
                "gate_rows": cal.gate.rows,
                "asset": f.asset,
                "asset_source": f.asset_source,
                "override_reason": reason if flags else None,
                **self.settings.extra,
            }
            claim = Claim(
                claim_id=f.claim_id,
                agent_id=f.agent_id,
                model_version=f.model_version,
                code_version=self.settings.code_version,
                as_of=f.as_of,
                data_cutoff=f.data_cutoff or f.as_of,
                target_name=f.target_name,
                unit=f.unit,
                target_start=f.target_start,
                target_end=f.target_end,
                point=f.point,
                lower=f.point - half,
                upper=f.point + half,
                coverage=self.settings.coverage,
                reference_class=f.day_type,
                resolution_rule=RESOLUTION_RULE,
                input_data_hash=f.input_hash(),
                context=context,
            )
            led.add_claim(claim)
            return receipt(claim, new=True)

    def resolve(self, o: Outcome, ledger: Ledger | None = None) -> dict:
        """Score a recorded forecast against what really happened."""
        with self._ledger(ledger) as led:
            claim = led.get_claim(o.claim_id)
            res = led.resolve(o.claim_id, o.realised, o.fingerprint(), o.realised_class)
            out = {
                "claim_id": claim.claim_id,
                "realised": res.realised_value,
                "resolution_version": res.resolution_version,
                "calibrated": claim.context["calibration"]["method"] != "none",
                "covered": is_covered(claim, res),
                "below_lower": res.realised_value < claim.lower,
                "interval_score": round(interval_score(claim, res), 6),
            }
            agent = claim.context.get("agent_range")
            if agent is not None:
                out["agent_range_covered"] = agent[0] <= res.realised_value <= agent[1]
            return out

    def replay(self, forecasts: Iterable[Forecast], outcomes: Iterable[Outcome]) -> dict:
        """Run a forecaster's history through Calibra in time order, as if live: before each
        forecast, only outcomes whose target period has ended are resolved."""
        todo = sorted(forecasts, key=lambda f: (f.as_of, f.target_start, f.forecast_id))
        known = {o.claim_id: o for o in outcomes}
        pending: list[Forecast] = []
        counts = {"submitted": 0, "resolved": 0, "awaiting_outcome": 0}
        with self._ledger() as led:

            def settle(until: datetime | None) -> None:
                for p in sorted(pending, key=lambda x: x.target_end):
                    if until is not None and p.target_end > until:
                        continue
                    pending.remove(p)
                    if p.claim_id in known:
                        self.resolve(known[p.claim_id], led)
                        counts["resolved"] += 1
                    else:
                        counts["awaiting_outcome"] += 1

            for f in todo:
                settle(f.as_of)
                self.submit(f, ledger=led)
                counts["submitted"] += 1
                pending.append(f)
            settle(None)
            counts["chain_head"] = led.head()
        return counts

    # ---- reading back -------------------------------------------------------------------

    def frame(self, agent_id: str | None = None, ledger: Ledger | None = None) -> pd.DataFrame:
        """One row per resolved BYOF claim (latest resolution)."""
        q = f"""
            SELECT c.claim_id, c.agent_id, c.target_start, c.recorded_at, c.point, c.lower,
                   c.upper, c.coverage, c.reference_class, c.context, r.realised_value, c.unit
            FROM claims c JOIN ({LATEST}) r USING (claim_id)
            {"WHERE c.agent_id = ?" if agent_id else ""}
            ORDER BY c.target_start
        """
        with self._ledger(ledger) as led:
            rows = led.con.execute(q, [agent_id] if agent_id else []).fetchall()
        recs = []
        for cid, aid, start, recorded, point, lo, hi, cov, cls, ctx, real, unit in rows:
            c = json.loads(ctx)
            if c.get("source") != SOURCE:
                continue
            agent = c.get("agent_range")
            recs.append(
                {
                    "claim_id": cid,
                    "agent": aid,
                    "target_start": start,
                    "live": recorded <= start,
                    "day_type": cls,
                    "point": point,
                    "unit": unit,
                    "coverage": cov,
                    "lower": lo,
                    "upper": hi,
                    "realised": real,
                    "calibrated": c["calibration"]["method"] != "none",
                    "method": c["calibration"]["method"],
                    "in_scope": bool(c.get("in_scope", True)),
                    "covered": lo <= real <= hi,
                    "agent_lower": None if agent is None else agent[0],
                    "agent_upper": None if agent is None else agent[1],
                }
            )
        return pd.DataFrame(recs)

    def report(self, agent_id: str) -> dict:
        """The agent's track record: honesty of Calibra's range and of its own, in money too."""
        df = self.frame(agent_id)
        if df.empty:
            raise ValueError(f"no resolved forecasts for agent {agent_id!r}")
        cal = df[df["calibrated"]]
        out: dict = {
            "agent_id": agent_id,
            "resolved": len(df),
            "live": int(df["live"].sum()),
            "calibrated": len(cal),
            "promised_coverage": float(df["coverage"].iloc[0]),
            "methods": df["method"].value_counts().to_dict(),
        }
        if len(cal):
            fp = floor_promise(cal.assign(agent=agent_id)).iloc[0]
            out["calibra_range"] = {
                "coverage": round(float(cal["covered"].mean()), 4),
                "mean_width": round(float((cal["upper"] - cal["lower"]).mean()), 6),
                "promise_broken_share": round(float(fp["broken_share"]), 4),
                "shortfall": round(float(fp["shortfall_gbp"]), 6),
            }
            by_scope = shortfall_by_scope(cal)
            out["by_scope"] = {
                str(k): {
                    "forecasts": int(v["days"]),
                    "broken": int(v["broken_days"]),
                    "shortfall": round(float(v["shortfall_gbp"]), 6),
                    "share_of_shortfall": None
                    if math.isnan(v["share_of_shortfall"])
                    else round(float(v["share_of_shortfall"]), 4),
                }
                for k, v in by_scope.iterrows()
            }
        own = cal.dropna(subset=["agent_lower"]) if len(cal) else cal
        if len(own):
            real = own["realised"]
            inside = (own["agent_lower"] <= real) & (real <= own["agent_upper"])
            out["agent_own_range"] = {
                "forecasts": len(own),
                "coverage": round(float(inside.mean()), 4),
                "mean_width": round(float((own["agent_upper"] - own["agent_lower"]).mean()), 6),
                "note": "same forecasts as calibra_range, scored on the agent's own range",
            }
        return out

    def agents(self) -> list[dict]:
        """Every agent in this ledger, with how many forecasts it has and how many are scored."""
        if not self.ledger_path.exists():
            return []
        q = f"""
            SELECT c.agent_id, count(*) AS forecasts, count(r.claim_id) AS resolved,
                   min(c.target_start) AS first, max(c.target_start) AS last
            FROM claims c LEFT JOIN ({LATEST}) r USING (claim_id)
            WHERE json_extract_string(c.context, '$.source') = ?
            GROUP BY c.agent_id ORDER BY c.agent_id
        """
        with self._ledger() as led:
            rows = led.con.execute(q, [SOURCE]).fetchall()
        return [
            {"agent_id": a, "forecasts": n, "resolved": r, "first": str(f), "last": str(la)}
            for a, n, r, f, la in rows
        ]

    def status(self, agent_id: str, as_of: datetime, day_type: str | None = None) -> dict:
        """What Calibra knows about this agent now: range method and width, scope bands."""
        cal = self.calibration(agent_id, as_of, day_type)
        return {
            "agent_id": agent_id,
            "as_of": as_of.isoformat(),
            "method": cal.method,
            "half_width": cal.half_width,
            "past_outcomes": cal.past_outcomes,
            "class_outcomes": cal.class_outcomes,
            "note": cal.note,
            "gate_bands": cal.gate.bands(),
        }

    def check_scope(self, agent_id: str, as_of: datetime, features: dict[str, float]) -> dict:
        gate = self.calibration(agent_id, as_of).gate
        flags = gate.flags(features)
        return {"in_scope": not flags, "flags": flags, "learned_on": gate.rows}


def receipt(claim: Claim, new: bool) -> dict:
    c = claim.context
    return {
        "claim_id": claim.claim_id,
        "new": new,
        "point": claim.point,
        "lower": claim.lower,
        "upper": claim.upper,
        "coverage": claim.coverage,
        "method": c["calibration"]["method"],
        "note": c["calibration"]["note"],
        "in_scope": c["in_scope"],
        "scope_flags": c["scope_flags"],
        "recorded_at": claim.recorded_at.isoformat(),
        "live": claim.is_live,
    }
