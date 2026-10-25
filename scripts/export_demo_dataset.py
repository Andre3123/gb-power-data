"""Export Pillswood's day-ahead track record as a customer's own files, for onboarding tests.

  forecasts.csv  one row per day: when it was made, the expected revenue, the day class,
                 the inputs the plan used (prices two days before) and the cycles planned
  actuals.csv    one row per day: the revenue the plan really earned

Run: uv run python scripts/export_demo_dataset.py demo_dataset
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from gb_power_data.claims.dayahead import UK
from gb_power_data.claims.run_backtest import LEDGER
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES
from gb_power_data.scopegate.gate import daily_stats

AGENT = "persistence-dispatch"

QUERY = """
SELECT c.target_start, c.as_of, c.point, c.reference_class, c.context, r.realised_value
FROM claims c JOIN (
    SELECT * FROM resolutions
    QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY resolution_version DESC) = 1
) r USING (claim_id)
WHERE c.agent_id = ?
ORDER BY c.target_start
"""


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_dataset")
    out.mkdir(parents=True, exist_ok=True)
    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date
    stats = daily_stats(prices)

    con = duckdb.connect(str(LEDGER), read_only=True)
    try:
        rows = con.execute(QUERY, [AGENT]).fetchall()
    finally:
        con.close()
    if not rows:
        sys.exit(f"no resolved {AGENT} claims in {LEDGER}: run gbpd-calibrate first")

    def num(v):
        return "" if v is None or pd.isna(v) else f"{v:.2f}"

    forecasts, actuals = [], []
    for start, as_of, point, cls, ctx, realised in rows:
        day = pd.Timestamp(start, tz="UTC").tz_convert(UK).date()
        made = pd.Timestamp(as_of, tz="UTC").tz_convert(UK)
        src = day - timedelta(days=2)
        s = stats.loc[src] if src in stats.index else None
        net = np.array(json.loads(ctx)["plan_net_mw"])
        cycles = float(np.clip(net, 0, None).sum() * PILLSWOOD.dt_h) / PILLSWOOD.energy_mwh
        forecasts.append({
            "delivery_day": day.isoformat(),
            "created_at": made.strftime("%Y-%m-%d %H:%M"),
            "expected_revenue_gbp": f"{point:.2f}",
            "day_class": cls,
            "da_spread_d2": num(None if s is None else s["spread"]),
            "max_price_d2": num(None if s is None else s["max_price"]),
            "min_price_d2": num(None if s is None else s["min_price"]),
            "spread_volatility_7d": num(None if s is None else s["volatility_7d"]),
            "planned_cycles": f"{cycles:.3f}",
        })
        actuals.append({"delivery_day": day.isoformat(), "actual_revenue_gbp": f"{realised:.2f}"})

    for name, data in (("forecasts.csv", forecasts), ("actuals.csv", actuals)):
        with (out / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    print(f"wrote {out}/forecasts.csv and {out}/actuals.csv: {len(forecasts)} days, "
          f"{forecasts[0]['delivery_day']} to {forecasts[-1]['delivery_day']}")


if __name__ == "__main__":
    main()
