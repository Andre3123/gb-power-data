"""Make, record and resolve day-ahead claims over a date range.

Run: uv run gbpd-backtest 2025-01-01 2025-01-31
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES

LEDGER = Path("data/ledger/ledger.duckdb")


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def main() -> None:
    start, end = (pd.Timestamp(a).date() for a in sys.argv[1:3])
    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(LEDGER)
    summary = backtest(prices, start, end, PILLSWOOD, ledger, code_version=git_commit())
    ledger.close()

    print(f"{summary.claims} claims resolved, {summary.skipped} days skipped")
    print(f"coverage {summary.coverage:.0%} (promised 90%)")
    print(f"mean interval score {summary.mean_interval_score:,.0f}")
    print(summary.by_class.round(2).to_string())


if __name__ == "__main__":
    main()
