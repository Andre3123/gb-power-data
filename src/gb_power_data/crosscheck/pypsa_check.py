"""Cross-check of the Pillswood dispatch against PyPSA, solved by HiGHS.

For one day's half-hour prices, build a one-bus PyPSA network: a `market` Generator that can
sell to or buy from the bus at the period's price, and one `StorageUnit` with the same numbers
as `optimise.run_day.PILLSWOOD`: power, max_hours = energy / power, charge and discharge
efficiencies, starting state of charge, and wear cost as the discharge marginal cost.

Constraints matched to `optimise.dispatch` through `extra_functionality`:
- the daily cycle cap IS included, as a global constraint on energy discharged in the day;
- the end-of-day rule (end with at least the starting energy) is included.

Not matched: PyPSA's StorageUnit is a plain LP, so it may charge and discharge in the same
period, which our MILP forbids. With the wear cost on discharge this only pays when the
battery is full and prices are very negative, so the two models can differ on such days.
The connection limit is not modelled separately because Pillswood's grid_mw equals power_mw.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import pandas as pd
import pypsa

from gb_power_data.optimise.dispatch import Site
from gb_power_data.optimise.run_day import PILLSWOOD


@dataclass(frozen=True)
class PypsaDispatch:
    schedule: pd.DataFrame  # price, charge_mw, discharge_mw, energy_mwh per period
    revenue_gbp: float  # market revenue, before wear, as optimise.dispatch.solve_day


def build_network(prices: pd.Series, site: Site = PILLSWOOD) -> pypsa.Network:
    if prices.empty or prices.isna().any():
        raise ValueError("prices must be non-empty with no gaps")
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(prices), name="snapshot"))
    n.snapshot_weightings.loc[:, :] = site.dt_h
    n.add("Carrier", "AC")
    n.add("Bus", "bus", carrier="AC")
    price = pd.Series(prices.to_numpy(dtype=float), index=n.snapshots)
    trade_mw = 10 * (site.power_mw + (site.grid_mw or site.power_mw))
    n.add(
        "Generator",
        "market",
        bus="bus",
        p_nom=trade_mw,
        p_min_pu=-1.0,
        marginal_cost=price,
    )
    n.add(
        "StorageUnit",
        "battery",
        bus="bus",
        p_nom=site.power_mw,
        max_hours=site.energy_mwh / site.power_mw,
        p_min_pu=-1.0,
        p_max_pu=1.0,
        efficiency_store=site.eta_charge,
        efficiency_dispatch=site.eta_discharge,
        state_of_charge_initial=site.soc_start * site.energy_mwh,
        cyclic_state_of_charge=False,
        marginal_cost=site.degradation_gbp_per_mwh,
    )
    return n


def _extra_constraints(site: Site):
    e0 = site.soc_start * site.energy_mwh

    def add(n: pypsa.Network, snapshots: pd.Index) -> None:
        m = n.model
        soc = m.variables["StorageUnit-state_of_charge"]
        m.add_constraints(soc.isel(snapshot=-1) >= e0, name="end_energy")
        if site.soc_min > 0:
            m.add_constraints(soc >= site.soc_min * site.energy_mwh, name="soc_min")
        if site.soc_max < 1:
            m.add_constraints(soc <= site.soc_max * site.energy_mwh, name="soc_max")
        if site.max_cycles_per_day is not None:
            dispatch = m.variables["StorageUnit-p_dispatch"]
            cap = site.max_cycles_per_day * site.energy_mwh
            m.add_constraints((site.dt_h * dispatch).sum() <= cap, name="cycle_cap")

    return add


def solve_day_pypsa(prices: pd.Series, site: Site = PILLSWOOD) -> PypsaDispatch:
    loggers = [logging.getLogger(name) for name in ("pypsa", "linopy")]
    levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            n = build_network(prices, site)
            status, condition = n.optimize(
                solver_name="highs",
                extra_functionality=_extra_constraints(site),
                include_objective_constant=False,
                output_flag=False,
            )
    finally:
        for lg, level in zip(loggers, levels, strict=True):
            lg.setLevel(level)
    if status != "ok":
        raise RuntimeError(f"PyPSA solve failed: {status} / {condition}")

    schedule = pd.DataFrame(
        {
            "price": prices.to_numpy(dtype=float),
            "charge_mw": n.storage_units_t.p_store["battery"].to_numpy(),
            "discharge_mw": n.storage_units_t.p_dispatch["battery"].to_numpy(),
            "energy_mwh": n.storage_units_t.state_of_charge["battery"].to_numpy(),
        },
        index=prices.index,
    )
    net_mw = schedule.discharge_mw - schedule.charge_mw
    revenue = float((schedule.price * net_mw * site.dt_h).sum())
    return PypsaDispatch(schedule=schedule, revenue_gbp=revenue)


def pypsa_revenue(prices: pd.Series, site: Site = PILLSWOOD) -> float:
    """Market revenue (price x net export x dt, before wear) of PyPSA's optimal plan."""
    return solve_day_pypsa(prices, site).revenue_gbp
