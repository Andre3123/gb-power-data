"""What the claims are worth in pounds: the floor promise and the capture rate.

Reads the ledger read-only. The best possible plan of each day (perfect foresight) is solved
once and cached in data/processed/, so the first run is slow (about a second per day) and later
runs are fast.

Run: uv run gbpd-value 2025-01-01 2026-09-30
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from gb_power_data.claims.dayahead import UK, day_prices
from gb_power_data.claims.run_backtest import LEDGER
from gb_power_data.optimise.dispatch import Site, solve_day
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES
from gb_power_data.report.data import ReportError, load
from gb_power_data.value.money import capture, floor_promise, wear_cost

PLAN_AGENT = "persistence-dispatch"  # all three agents share this plan; only the ranges differ


def _plans(start: date, end: date) -> pd.DataFrame:
    """The claimed plan and its realised market revenue (latest resolution), one row per day."""
    q = """
    SELECT c.target_start, c.reference_class, c.context, r.realised_value
    FROM claims c JOIN (
        SELECT * FROM resolutions
        QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY resolution_version DESC) = 1
    ) r USING (claim_id)
    WHERE c.agent_id = ?
    ORDER BY c.target_start
    """
    con = duckdb.connect(str(LEDGER), read_only=True)
    try:
        rows = con.execute(q, [PLAN_AGENT]).fetchall()
    finally:
        con.close()
    out = []
    for ts, cls, ctx, realised in rows:
        d = pd.Timestamp(ts, tz="UTC").tz_convert(UK).date()
        if start <= d <= end:
            net = np.array(json.loads(ctx)["plan_net_mw"])
            out.append({"day": d, "day_type": cls, "realised_market_gbp": realised, "net": net})
    return pd.DataFrame(out)


def _cache_path(site: Site) -> Path:
    key = hashlib.sha256(repr(site).encode()).hexdigest()[:8]  # new file if the site changes
    return Path(f"data/processed/perfect_foresight_{key}.parquet")


def _perfect(prices: pd.DataFrame, days: list[date], site: Site) -> pd.DataFrame:
    """Best possible net revenue of each day (plan solved on the day's real prices)."""
    path = _cache_path(site)
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["day"])
    done = set(pd.to_datetime(cached["day"]).dt.date)
    new = []
    todo = [d for d in days if d not in done]
    for i, d in enumerate(todo, 1):
        best = solve_day(day_prices(prices, d), site)
        net = (best.schedule.discharge_mw - best.schedule.charge_mw).to_numpy()
        wear = wear_cost(net, site.dt_h, site.degradation_gbp_per_mwh)
        new.append({"day": d, "perfect_net_gbp": best.revenue_gbp - wear})
        if i % 50 == 0:
            print(f"  best possible plans: {i}/{len(todo)}", file=sys.stderr)
    if new:
        cached = pd.concat([cached, pd.DataFrame(new)], ignore_index=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        cached.to_parquet(path)
    cached["day"] = pd.to_datetime(cached["day"]).dt.date
    return cached[cached["day"].isin(days)]


def main() -> None:
    sys.stdout.reconfigure(errors="replace")  # Windows consoles may not print "±"
    start, end = (pd.Timestamp(a).date() for a in sys.argv[1:3])
    try:
        claims = load(LEDGER, start, end).claims
    except ReportError as e:
        sys.exit(f"gbpd-value: error: {e}")

    print("1. Floor promise: the owner promises the claim's lower bound as safe revenue\n")
    fp = floor_promise(claims)
    print(fp.round(2).to_string())

    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date
    plans = _plans(start, end)
    site = PILLSWOOD
    plans["realised_net_gbp"] = plans["realised_market_gbp"] - [
        wear_cost(n, site.dt_h, site.degradation_gbp_per_mwh) for n in plans["net"]
    ]
    best = _perfect(prices, list(plans["day"]), site)
    days = plans.merge(best, on="day")
    cap = capture(days)
    print(f"\n2. Capture rate, net of wear at GBP {site.degradation_gbp_per_mwh:.0f}/MWh\n")
    print(cap.round(3).to_string())

    years = len(days) / 365.25
    print(
        f"\nPer year ({len(days)} days = {years:.2f} years):"
        f" earned GBP {cap.loc['all', 'earned_net_gbp'] / years:,.0f},"
        f" possible GBP {cap.loc['all', 'possible_net_gbp'] / years:,.0f},"
        f" left on the table GBP {cap.loc['all', 'left_on_table_gbp'] / years:,.0f}"
    )
    print(
        "\nNotes: day-ahead arbitrage only, on APX prices; no balancing or ancillary revenue."
        "\nEfficiency, wear and cycle cap are placeholders (see physics/scope.py)."
    )


if __name__ == "__main__":
    main()