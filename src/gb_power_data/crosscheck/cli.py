"""Compare our dispatch revenue with PyPSA's, day by day, on real APX prices.

run: uv run gbpd-crosscheck 2025-01-01 2025-01-31
"""

from __future__ import annotations

import argparse
import logging
import math

import pandas as pd

from gb_power_data.crosscheck.pypsa_check import pypsa_revenue
from gb_power_data.optimise.dispatch import solve_day
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES


def compare(df: pd.DataFrame, start, end) -> pd.DataFrame:
    df = df.copy()
    df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
    days = df[(df["settlement_date"] >= start) & (df["settlement_date"] <= end)]
    rows = []
    for day, g in days.groupby("settlement_date", sort=True):
        prices = g.sort_values("settlement_period").set_index("settlement_period")["price"]
        if prices.isna().any():
            print(f"{day}: skipped, {int(prices.isna().sum())} missing prices")
            continue
        ours = solve_day(prices, PILLSWOOD).revenue_gbp
        theirs = pypsa_revenue(prices, PILLSWOOD)
        gap = theirs - ours
        pct = 100 * gap / abs(ours) if ours else math.nan
        rows.append({"day": day, "ours": ours, "pypsa": theirs, "gap_gbp": gap, "gap_pct": pct})
    return pd.DataFrame(rows, columns=["day", "ours", "pypsa", "gap_gbp", "gap_pct"])


def main() -> None:
    p = argparse.ArgumentParser(description="Cross-check Pillswood dispatch against PyPSA.")
    p.add_argument("start", type=lambda s: pd.Timestamp(s).date())
    p.add_argument("end", type=lambda s: pd.Timestamp(s).date())
    p.add_argument("--prices", default=PRICES)
    args = p.parse_args()
    logging.getLogger("linopy").setLevel(logging.WARNING)

    res = compare(pd.read_parquet(args.prices), args.start, args.end)
    if res.empty:
        raise SystemExit(f"no prices between {args.start} and {args.end}")
    print(f"{'day':<12}{'ours GBP':>14}{'PyPSA GBP':>14}{'gap GBP':>12}{'gap %':>9}")
    for r in res.itertuples():
        print(f"{r.day!s:<12}{r.ours:>14,.0f}{r.pypsa:>14,.0f}{r.gap_gbp:>12,.2f}{r.gap_pct:>9.3f}")
    worst = res.loc[res.gap_gbp.abs().idxmax()]
    print(
        f"largest gap: {worst.day} GBP {worst.gap_gbp:,.2f} ({worst.gap_pct:.3f}%)"
        f" | median gap: GBP {res.gap_gbp.abs().median():,.2f}"
        f" ({res.gap_pct.abs().median():.3f}%) over {len(res)} days"
    )


if __name__ == "__main__":
    main()
