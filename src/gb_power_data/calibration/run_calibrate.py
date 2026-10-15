"""Learn range widths on a past period, then claim and score a test period with them.

Run: uv run gbpd-calibrate 2023-01-01 2024-12-31 2025-01-01 2025-01-31
     (learning start, learning end, test start, test end)
"""

from __future__ import annotations

import sys

import pandas as pd

from gb_power_data.calibration.split_conformal import (
    conformal_half_width,
    mondrian_half_widths,
    residuals_by_class,
    residuals_from_ledger,
)
from gb_power_data.claims.dayahead import backtest
from gb_power_data.claims.run_backtest import LEDGER, git_commit
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES

COVERAGE = 0.90


def main() -> None:
    learn_start, learn_end, test_start, test_end = (pd.Timestamp(a).date() for a in sys.argv[1:5])
    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(LEDGER)
    commit = git_commit()

    # 1. naive claims over the learning period, so their mistakes can be measured
    backtest(prices, learn_start, learn_end, PILLSWOOD, ledger, commit)
    residuals = residuals_from_ledger(ledger, "persistence-dispatch", learn_start, learn_end)
    q = conformal_half_width(residuals, COVERAGE)
    print(f"split conformal: {len(residuals)} past claims -> half-width GBP {q:,.0f}")

    # 2. the same mistakes, sorted into one pile per day type (Mondrian)
    by_class = residuals_by_class(ledger, "persistence-dispatch", learn_start, learn_end)
    qs = mondrian_half_widths(by_class, COVERAGE)
    for cls, q_cls in qs.items():
        print(f"mondrian {cls:>7}: {len(by_class[cls])} past claims -> half-width GBP {q_cls:,.0f}")

    # 3. the same test period, claimed three times
    naive = backtest(prices, test_start, test_end, PILLSWOOD, ledger, commit)
    split = backtest(prices, test_start, test_end, PILLSWOOD, ledger, commit, "persistence-cp", q)
    mond = backtest(prices, test_start, test_end, PILLSWOOD, ledger, commit, "persistence-mcp", qs)
    ledger.close()

    runs = {"naive +-40%": naive, "split conformal": split, "mondrian": mond}
    table = pd.DataFrame(
        {name: [r.claims, r.coverage, r.mean_interval_score] for name, r in runs.items()},
        index=["claims", "coverage (promised 0.90)", "mean interval score"],
    )
    print("\n" + table.round(2).to_string())
    for name in ("split conformal", "mondrian"):
        print(f"\nby day type, {name}:")
        print(runs[name].by_class.round(2).to_string())


if __name__ == "__main__":
    main()