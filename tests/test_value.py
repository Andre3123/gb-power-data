import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.run_day import PILLSWOOD
from gb_power_data.value import run_value
from gb_power_data.value.money import (
    capture,
    floor_promise,
    shortfall_by_scope,
    wear_cost,
    worst_days,
)

STEP = np.r_[np.full(16, 40.0), np.full(16, 60.0), np.full(16, 150.0)]


def test_floor_promise_counts_only_the_money_below_the_floor():
    df = pd.DataFrame(
        {
            "agent": ["a", "a", "a", "a"],
            "lower": [100.0, 100.0, -50.0, -50.0],  # a negative lower bound promises nothing
            "realised": [150.0, 70.0, -20.0, -80.0],
        }
    )
    row = floor_promise(df).loc["a"]
    assert row["promised_gbp"] == 200.0
    # 100 - 70 = 30; the -20 loss was warned about (above -50); -80 is 30 below its bound
    assert row["shortfall_gbp"] == 30.0 + 30.0
    assert row["broken_days"] == 2


def test_wear_counts_discharge_only():
    net = np.array([98.0, -98.0, 0.0])  # one half-hour out, one in
    assert wear_cost(net, dt_h=0.5, gbp_per_mwh=15.0) == pytest.approx(15 * 49)


def test_capture_is_a_ratio_of_sums():
    df = pd.DataFrame(
        {
            "day_type": ["calm", "wild"],
            "realised_net_gbp": [1.0, 90.0],
            "perfect_net_gbp": [10.0, 100.0],  # 10% and 90% days
        }
    )
    assert capture(df).loc["all", "capture_rate"] == pytest.approx(91 / 110)


def test_cli_end_to_end_on_a_small_ledger(tmp_path, monkeypatch, capsys):
    shutil.copytree(Path("config"), tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    start = date(2025, 1, 3)
    days = [start + timedelta(days=i) for i in range(-2, 3)]
    prices = pd.DataFrame(
        [
            {"settlement_date": d, "settlement_period": i + 1, "price": float(p) * (1 + k / 10)}
            for k, d in enumerate(days)
            for i, p in enumerate(STEP)
        ]
    )
    Path("data/processed").mkdir(parents=True)
    prices.to_parquet(run_value.PRICES)
    Path("data/ledger").mkdir(parents=True)
    ledger = Ledger(run_value.LEDGER)
    backtest(prices, start, days[-1], PILLSWOOD, ledger, "test")
    ledger.close()

    monkeypatch.setattr("sys.argv", ["gbpd-value", str(start), str(days[-1])])
    run_value.main()
    out = capsys.readouterr().out
    assert "Floor promise" in out and "Capture rate" in out
    assert len(list(Path("data/processed").glob("perfect_foresight_*.parquet"))) == 1


def test_scope_split_shows_where_the_money_went_missing():
    df = pd.DataFrame(
        {
            "day": [1, 2, 3],
            "lower": [100.0, 100.0, 100.0],
            "realised": [90.0, 120.0, 10.0],  # shortfalls 10, 0, 90
            "in_scope": [True, True, False],
        }
    )
    by = shortfall_by_scope(df)
    assert by.loc["flagged", "share_of_shortfall"] == pytest.approx(0.9)
    assert worst_days(df, n=1)["day"].iloc[0] == 3