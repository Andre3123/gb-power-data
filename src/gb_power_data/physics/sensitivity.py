"""Physics sensitivity: how much a claim depends on battery parameters we have not verified.

The plan is re-solved under the plausible range of each uncertain physical parameter, and the
net revenue (market revenue minus wear) of each plan is compared. A wide spread means the claim
rests on the asset's physics as much as on prices, so measuring the asset is worth money.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from gb_power_data.optimise.dispatch import Site, solve_day

# Efficiency each way. 0.933 = sqrt(0.87): Tesla's Megapack datasheet gives 87% round trip for
# the 2-hour configuration, including thermal management, at 25 C, day 1. 0.95 is the model's
# placeholder (90% round trip, optimistic); 0.90 (81%) is near warranted targets (ACCURE 2026:
# recent contracts commonly ~83%).
EFFICIENCY = (0.90, 0.933, 0.95)
# Wear cost per MWh discharged: no public figure for Pillswood (heuristic range).
WEAR_GBP_PER_MWH = (5.0, 15.0, 30.0)  # the placeholder is 15
# Energy really dispatchable vs nameplate. ACCURE 2026 (Banking BESS): live operation runs a
# median 6.9% below the capacity test, the weakest decile 12% below.
ENERGY_FACTOR = (0.931, 0.88)
# Starting state-of-charge offsets. ACCURE 2026: median gap of 5.6 points between the battery's
# reported and measured SoC. Used here as a proxy for what an SoC error puts at stake.
SOC_OFFSET = (-0.056, 0.056)


@dataclass(frozen=True)
class PhysicsCase:
    efficiency: float
    wear_gbp_per_mwh: float
    energy_factor: float
    soc_start: float
    market_revenue_gbp: float
    wear_cost_gbp: float
    net_revenue_gbp: float
    cycles: float  # MWh discharged / nameplate energy


def _case(prices: pd.Series, site: Site, nameplate_mwh: float, **change) -> PhysicsCase:
    eta = change.pop("efficiency", site.eta_charge)
    s = replace(site, eta_charge=eta, eta_discharge=eta, **change)
    plan = solve_day(prices, s)
    discharged = float(plan.schedule.discharge_mw.sum() * s.dt_h)
    wear = s.degradation_gbp_per_mwh * discharged
    return PhysicsCase(
        efficiency=eta,
        wear_gbp_per_mwh=s.degradation_gbp_per_mwh,
        energy_factor=s.energy_mwh / nameplate_mwh,
        soc_start=s.soc_start,
        market_revenue_gbp=plan.revenue_gbp,
        wear_cost_gbp=wear,
        net_revenue_gbp=plan.revenue_gbp - wear,
        cycles=discharged / nameplate_mwh,
    )


def physics_cases(prices: pd.Series, site: Site) -> list[PhysicsCase]:
    """Every efficiency x wear combination, then energy and SoC one at a time around the site."""
    cases = [
        _case(prices, site, site.energy_mwh, efficiency=eta, degradation_gbp_per_mwh=wear)
        for eta in EFFICIENCY
        for wear in WEAR_GBP_PER_MWH
    ]
    for f in ENERGY_FACTOR:
        cases.append(_case(prices, site, site.energy_mwh, energy_mwh=site.energy_mwh * f))
    for d in SOC_OFFSET:
        soc = min(max(site.soc_start + d, site.soc_min), site.soc_max)
        cases.append(_case(prices, site, site.energy_mwh, soc_start=soc))
    return cases


def physics_spread(cases: list[PhysicsCase], site: Site) -> dict[str, float]:
    """How far apart the plausible physics put the claim, and which parameter drives it."""
    df = pd.DataFrame([c.__dict__ for c in cases])
    grid = df[(df["energy_factor"] == 1.0) & (df["soc_start"] == site.soc_start)]
    base = grid[
        (grid["efficiency"] == site.eta_charge)
        & (grid["wear_gbp_per_mwh"] == site.degradation_gbp_per_mwh)
    ]["net_revenue_gbp"]

    def effect(part: pd.Series) -> float:  # range of net revenue, the site's own case included
        values = pd.concat([part, base])
        return float(values.max() - values.min())

    net = df["net_revenue_gbp"]
    return {
        "net_min_gbp": float(net.min()),
        "net_max_gbp": float(net.max()),
        "spread_gbp": float(net.max() - net.min()),
        "wear_effect_gbp": float(
            grid.groupby("efficiency")["net_revenue_gbp"].agg(lambda s: s.max() - s.min()).mean()
        ),
        "efficiency_effect_gbp": float(
            grid.groupby("wear_gbp_per_mwh")["net_revenue_gbp"]
            .agg(lambda s: s.max() - s.min())
            .mean()
        ),
        "energy_effect_gbp": effect(df[df["energy_factor"] < 1.0]["net_revenue_gbp"]),
        "soc_effect_gbp": effect(df[df["soc_start"] != site.soc_start]["net_revenue_gbp"]),
        "cycles_min": float(df["cycles"].min()),
        "cycles_max": float(df["cycles"].max()),
    }
