"""Day-ahead revenue claims for Pillswood, written to the ledger and resolved on real prices.

The agent plans day D on the day before at 00:00 UK time. The newest complete day it can see
then is D-2, so the price forecast is D-2's prices (persistence). The claim is the plan's
expected revenue; the resolution is what the same, fixed plan earns on D's real prices.

"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from gb_power_data.ledger.models import Claim
from gb_power_data.ledger.scoring import interval_score, is_covered
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.dispatch import Site, solve_day
from gb_power_data.physics.scope import assumptions, physical_flags
from gb_power_data.scopegate.gate import ScopeGate

UK = ZoneInfo("Europe/London")
CLASSES = Path("config/reference_classes.toml")
NAIVE_WIDTH = 0.4  # heuristic placeholder range of +-40%; the calibration engine replaces it


def uk_midnight(d: date) -> datetime:
    """Start of a GB settlement day, in UTC."""
    return datetime.combine(d, time(0), tzinfo=UK).astimezone(UTC)


def day_prices(prices: pd.DataFrame, d: date) -> pd.Series:
    day = prices[prices["settlement_date"] == d].sort_values("settlement_period")
    return day.set_index("settlement_period")["price"]


def fingerprint(p: pd.Series) -> str:
    """Short SHA-256 of the exact price values used."""
    return hashlib.sha256(p.to_numpy(dtype=float).tobytes()).hexdigest()[:16]


def spread_class(p: pd.Series) -> str:
    """Pre-registered class of a day by its price spread (config/reference_classes.toml)."""
    cuts = tomllib.loads(CLASSES.read_text())["spread"]
    spread = p.max() - p.min()
    if spread < cuts["calm_below"]:
        return "calm"
    return "wild" if spread > cuts["wild_above"] else "normal"


def plan_revenue(net_mw: np.ndarray, p: pd.Series, dt_h: float) -> float:
    """Market revenue of a fixed plan (+ export, - import, in MW) at the given prices."""
    return float((p.to_numpy(dtype=float) * net_mw * dt_h).sum())


def make_claim(
    prices: pd.DataFrame,
    d: date,
    site: Site,
    code_version: str,
    label: str = "persistence",
    half_width: float | dict[str, float] | None = None,
    scope_flags: list[str] | None = None,
) -> Claim | None:
    """Claim for day d, or None if the forecast day is missing or has a different length
    (clock-change days have 46 or 50 periods, so persistence cannot be lined up).
    half_width None gives the naive +-40% range; a number gives point +- that many GBP;
    a dict gives one learned half-width per day type (Mondrian), picked by the forecast's class.
    scope_flags come from ScopeGate: an empty list means in scope; None means not checked."""
    source = d - timedelta(days=2)
    forecast, actual = day_prices(prices, source), day_prices(prices, d)
    if forecast.empty or actual.empty or len(forecast) != len(actual):
        return None
    plan = solve_day(forecast, site)
    net = (plan.schedule.discharge_mw - plan.schedule.charge_mw).to_numpy()
    point = plan.revenue_gbp
    if isinstance(half_width, dict):  # mondrian : the half-width learned
        half_width = half_width[spread_class(forecast)]
    half = NAIVE_WIDTH * abs(point) if half_width is None else half_width
    interval = f"naive +-{NAIVE_WIDTH:.0%}" if half_width is None else f"+-GBP {half_width:,.0f}"
    return Claim(
        claim_id=f"pillswood-{label}-{d}",
        agent_id=f"{label}-dispatch",
        model_version="0.1",
        code_version=code_version,
        as_of=uk_midnight(d - timedelta(days=1)),
        data_cutoff=uk_midnight(source + timedelta(days=1)),
        target_name="daily_market_revenue",
        unit="GBP",
        target_start=uk_midnight(d),
        target_end=uk_midnight(d + timedelta(days=1)),
        point=point,
        lower=point - half,
        upper=point + half,
        coverage=0.90,
        reference_class=spread_class(forecast),
        resolution_rule="market revenue of the claimed plan on realised APX prices, before wear",
        input_data_hash=fingerprint(forecast),
        context={
            "site": "Pillswood",
            "forecast": "persistence from D-2",
            "interval": interval,
            "dt_h": site.dt_h,
            "plan_net_mw": [round(x, 6) for x in net],
            "scope_flags": scope_flags,  # None: ScopeGate was not used
            "in_scope": not scope_flags,
            "physics": assumptions(site),  # the battery numbers this plan rests on
            "physical_flags": physical_flags(plan.schedule, site),
        },
    )


@dataclass(frozen=True)
class BacktestSummary:
    claims: int
    skipped: int
    coverage: float
    mean_interval_score: float
    by_class: pd.DataFrame
    by_scope: pd.DataFrame


def backtest(
    prices: pd.DataFrame,
    start: date,
    end: date,
    site: Site,
    ledger: Ledger,
    code_version: str,
    label: str = "persistence",
    half_width: float | dict[str, float] | None = None,
    gate: ScopeGate | None = None,
) -> BacktestSummary:
    rows, skipped = [], 0
    for d in pd.date_range(start, end).date:
        claim_id = f"pillswood-{label}-{d}"
        try:
            claim = ledger.get_claim(claim_id)  # already claimed in an earlier run
        except KeyError:
            flags = gate.flags(d) if gate is not None else None
            new = make_claim(prices, d, site, code_version, label, half_width, flags)
            if new is None:
                skipped += 1
                continue
            ledger.add_claim(new)
            claim = new
        actual = day_prices(prices, d)
        realised = plan_revenue(np.array(claim.context["plan_net_mw"]), actual, site.dt_h)
        res = ledger.resolve(
            claim_id,
            realised,
            truth_data_hash=fingerprint(actual),
            realised_class=spread_class(actual),
        )
        rows.append(
            {
                "day": d,
                "class": claim.reference_class,
                "in_scope": claim.context.get("in_scope", True),
                "has_negative": bool((actual < 0).any()),
                "covered": is_covered(claim, res),
                "interval_score": interval_score(claim, res),
            }
        )
    if not rows:
        raise ValueError(f"no claims could be made between {start} and {end}")
    df = pd.DataFrame(rows)
    by_class = df.groupby("class").agg(
        claims=("covered", "size"),
        coverage=("covered", "mean"),
        mean_score=("interval_score", "mean"),
    )
    by_scope = df.groupby("in_scope").agg(
        claims=("covered", "size"),
        coverage=("covered", "mean"),
        mean_score=("interval_score", "mean"),
        share_of_bill=("interval_score", "sum"),
    )
    by_scope["share_of_bill"] /= df["interval_score"].sum()
    return BacktestSummary(
        claims=len(df),
        skipped=skipped,
        coverage=float(df["covered"].mean()),
        mean_interval_score=float(df["interval_score"].mean()),
        by_class=by_class,
        by_scope=by_scope,
    )
