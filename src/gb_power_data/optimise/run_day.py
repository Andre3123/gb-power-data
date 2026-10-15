"""the run the dispatch on the real settlement day of APX prices.
run: uv run gbpd-dispatch 2025-01-08
"""

from __future__ import annotations

import sys

import pandas as pd

from gb_power_data.optimise.dispatch import Site, shadow_prices, solve_day

PRICES = "data/processed/prices_apx.parquet"

# Pillswood: 98 MW / 196 MWh verified (data/sites/pillswood.toml).
# Efficiencies are UNVERIFIED placeholders: 0.95 each way, about 0.90 round trip.

PILLSWOOD = Site(
    power_mw=98,
    energy_mwh=196,
    grid_mw=98,
    eta_charge=0.95,
    eta_discharge=0.95,
    degradation_gbp_per_mwh=15.0,
    max_cycles_per_day=1.5,
)


def main() -> None:
    day = pd.Timestamp(sys.argv[1]).date()
    df = pd.read_parquet(PRICES)
    df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
    prices = df[df["settlement_date"] == day].sort_values("settlement_period")
    if prices.empty:
        raise SystemExit(f"no prices for {day}")

    result = solve_day(prices.set_index("settlement_period")["price"], PILLSWOOD)
    s = result.schedule.copy()
    s["shadow_price"] = shadow_prices(result.schedule.price, PILLSWOOD, result)
    print(f"{day}: {len(s)} periods, perfect-foresight revenue GBP {result.revenue_gbp:,.0f}")
    charged = s.charge_mw.sum() * PILLSWOOD.dt_h
    discharged = s.discharge_mw.sum() * PILLSWOOD.dt_h
    wear = discharged * PILLSWOOD.degradation_gbp_per_mwh
    print(f"charged {charged:,.0f} MWh, discharged {discharged:,.0f} MWh")
    print(f"wear cost GBP {wear:,.0f}, revenue after wear GBP {result.revenue_gbp - wear:,.0f}")
    print(f"periods charging and discharging at once: {result.simultaneous_periods}")
    active = s[(s.charge_mw > 1e-6) | (s.discharge_mw > 1e-6)].round(1)
    print(active.to_string())


if __name__ == "__main__":
    main()
