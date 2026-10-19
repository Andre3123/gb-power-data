"""How much of a claim's uncertainty comes from the battery's physics, not from prices?

For a sample of test days, re-plan each day's claim under the plausible physical parameters and
compare the spread with the price-based range Mondrian learned for that day type.

Run: uv run gbpd-physics 2025-01-01 2026-09-30 7     (start, end, every Nth day)
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import pandas as pd

from gb_power_data.calibration.split_conformal import mondrian_half_widths, residuals_by_class
from gb_power_data.claims.dayahead import day_prices, spread_class
from gb_power_data.claims.run_backtest import LEDGER
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES
from gb_power_data.physics.sensitivity import physics_cases, physics_spread

LEARN_START, LEARN_END = date(2023, 1, 1), date(2024, 12, 31)


def main() -> None:
    start, end = (pd.Timestamp(a).date() for a in sys.argv[1:3])
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date

    ledger = Ledger(LEDGER)
    widths = mondrian_half_widths(
        residuals_by_class(ledger, "persistence-dispatch", LEARN_START, LEARN_END), 0.90
    )
    ledger.close()

    rows = []
    for d in pd.date_range(start, end).date[::step]:
        forecast = day_prices(prices, d - timedelta(days=2))  # what the agent plans from
        if len(forecast) != 48:  # missing day or clock change
            continue
        spread = physics_spread(physics_cases(forecast, PILLSWOOD), PILLSWOOD)
        cls = spread_class(forecast)
        rows.append({"day": d, "class": cls, "price_range_gbp": 2 * widths[cls], **spread})

    df = pd.DataFrame(rows)
    df["physics_share"] = df["spread_gbp"] / df["price_range_gbp"]
    table = df.groupby("class").agg(
        days=("day", "size"),
        physics_spread_gbp=("spread_gbp", "median"),
        wear_effect_gbp=("wear_effect_gbp", "median"),
        efficiency_effect_gbp=("efficiency_effect_gbp", "median"),
        energy_effect_gbp=("energy_effect_gbp", "median"),
        soc_effect_gbp=("soc_effect_gbp", "median"),
        price_range_gbp=("price_range_gbp", "first"),
        physics_share=("physics_share", "median"),
        cycles_min=("cycles_min", "median"),
        cycles_max=("cycles_max", "median"),
    )
    print(f"{len(df)} sampled days; medians per day type (GBP per day)\n")
    print(table.round(2).to_string())
    print(
        "\nphysics_share = physics spread / width of the price-based 90% range."
        "\nAbove ~0.3, the asset's physics is a material part of the claim's uncertainty."
    )


if __name__ == "__main__":
    main()
