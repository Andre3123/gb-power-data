"""Single-battery arbitrage dispatch in linopy, solved by HiGHS.

One on/off variable per period stops charging and discharging at the same time (a MILP).
Assumes perfect knowledge of the day's prices and that the battery does not move them
(price-taker). Revenue from this model is therefore an upper bound, not a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass

import linopy
import numpy as np
import pandas as pd
import xarray as xr


@dataclass(frozen=True)
class Site:
    power_mw: float  # max charge or discharge power
    energy_mwh: float  # usable energy capacity
    eta_charge: float  # share of grid energy that reaches the cells
    eta_discharge: float  # share of cell energy that reaches the grid
    soc_start: float = 0.5  # state of charge at the start, as a fraction
    soc_min: float = 0.0
    soc_max: float = 1.0
    grid_mw: float | None = None  # connection limit; None means same as power_mw
    dt_h: float = 0.5  # length of one settlement period in hours
    degradation_gbp_per_mwh: float = 0.0  # wear cost per MWh discharged
    max_cycles_per_day: float | None = None  # full-capacity discharges allowed per day

    def __post_init__(self) -> None:
        if self.power_mw <= 0 or self.energy_mwh <= 0:
            raise ValueError("power_mw and energy_mwh must be positive")
        for name in ("eta_charge", "eta_discharge"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if not 0 <= self.soc_min <= self.soc_start <= self.soc_max <= 1:
            raise ValueError("need 0 <= soc_min <= soc_start <= soc_max <= 1")
        if self.grid_mw is not None and self.grid_mw <= 0:
            raise ValueError("grid_mw must be positive")
        if self.degradation_gbp_per_mwh < 0:
            raise ValueError("degradation_gbp_per_mwh cannot be negative")
        if self.max_cycles_per_day is not None and self.max_cycles_per_day < 0:
            raise ValueError("max_cycles_per_day cannot be negative")


@dataclass(frozen=True)
class Dispatch:
    schedule: pd.DataFrame  # price, charge_mw, discharge_mw, energy_mwh per period
    revenue_gbp: float
    simultaneous_periods: int  # periods charging and discharging at once; must be 0


def _build(prices: pd.Series, site: Site, mode: np.ndarray | None = None) -> linopy.Model:
    """Build the model. With `mode` given (1 = charging), the on/off choice is fixed and the
    model becomes a plain LP, which is what lets us read shadow prices."""
    if prices.empty or prices.isna().any():
        raise ValueError("prices must be non-empty with no gaps")
    n = len(prices)
    t = pd.RangeIndex(n, name="t")
    price = xr.DataArray(prices.to_numpy(dtype=float), coords=[t])
    e0 = site.soc_start * site.energy_mwh
    grid = site.power_mw if site.grid_mw is None else site.grid_mw

    m = linopy.Model()
    charge = m.add_variables(lower=0, upper=site.power_mw, coords=[t], name="charge")
    discharge = m.add_variables(lower=0, upper=site.power_mw, coords=[t], name="discharge")
    energy = m.add_variables(
        lower=site.soc_min * site.energy_mwh,
        upper=site.soc_max * site.energy_mwh,
        coords=[t],
        name="energy",
    )
    if mode is None:
        charging = m.add_variables(binary=True, coords=[t], name="charging")  # 1 = charge mode
    else:
        charging = xr.DataArray(mode.astype(float), coords=[t])

    # energy balance: energy[t] = energy[t-1] + charged in - discharged out
    # at t = 0 the shifted term is missing, so the start energy goes on the right-hand side
    start = xr.DataArray(np.r_[e0, np.zeros(n - 1)], coords=[t])
    m.add_constraints(
        energy
        - energy.shift(t=1)
        - site.eta_charge * site.dt_h * charge
        + (site.dt_h / site.eta_discharge) * discharge
        == start,
        name="balance",
    )
    # end the day with at least the starting energy, so the day is not paid for by emptying it
    m.add_constraints(energy.isel(t=-1) >= e0, name="end_energy")
    # one converter: in each period it either charges or discharges, never both
    m.add_constraints(charge <= site.power_mw * charging, name="charge_mode")
    m.add_constraints(discharge <= site.power_mw * (1 - charging), name="discharge_mode")
    # warranty-style cap on how much energy can be cycled through in a day
    if site.max_cycles_per_day is not None:
        cap = site.max_cycles_per_day * site.energy_mwh
        m.add_constraints((site.dt_h * discharge).sum() <= cap, name="cycle_cap")
    # net flow at the site meter stays within the connection
    m.add_constraints(discharge - charge <= grid, name="export_limit")
    m.add_constraints(charge - discharge <= grid, name="import_limit")

    wear = site.degradation_gbp_per_mwh * site.dt_h * discharge
    m.add_objective((price * site.dt_h * (discharge - charge) - wear).sum(), sense="max")
    return m


def _solve(m: linopy.Model) -> None:
    status, condition = m.solve(solver_name="highs", output_flag=False)
    if status != "ok":
        raise RuntimeError(f"solver failed: {status} / {condition}")


def solve_day(prices: pd.Series, site: Site) -> Dispatch:
    m = _build(prices, site)
    _solve(m)
    schedule = pd.DataFrame(
        {
            "price": prices.to_numpy(dtype=float),
            "charge_mw": m.variables["charge"].solution.to_numpy(),
            "discharge_mw": m.variables["discharge"].solution.to_numpy(),
            "energy_mwh": m.variables["energy"].solution.to_numpy(),
        },
        index=prices.index,
    )
    net_mw = schedule.discharge_mw - schedule.charge_mw
    revenue = float((schedule.price * net_mw * site.dt_h).sum())  # market revenue, before wear
    both = ((schedule.charge_mw > 1e-6) & (schedule.discharge_mw > 1e-6)).sum()
    return Dispatch(schedule=schedule, revenue_gbp=revenue, simultaneous_periods=int(both))


def shadow_prices(prices: pd.Series, site: Site, result: Dispatch) -> pd.Series:
    """Value of one more MWh stored in the battery at each period, in GBP per MWh.

    Binary variables have no shadow prices, so the charge/discharge modes are fixed at the
    optimal plan and the remaining LP is re-solved; its duals on the energy balance are the
    shadow prices (lambda in the Lagrangian of the storage problem).
    """
    mode = (result.schedule.charge_mw.to_numpy() > 1e-6).astype(float)
    m = _build(prices, site, mode=mode)
    _solve(m)
    dual = m.constraints["balance"].dual.to_numpy()
    return pd.Series(dual, index=prices.index, name="shadow_price_gbp_per_mwh")
