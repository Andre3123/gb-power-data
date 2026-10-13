from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from gb_power_data.claims.dayahead import backtest, fingerprint, make_claim, uk_midnight
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.dispatch import Site

SITE = Site(power_mw=98, energy_mwh=196, eta_charge=0.95, eta_discharge=0.95)
DAY_SHAPE = np.r_[np.full(16, 40.0), np.full(16, 60.0), np.full(16, 150.0)]


def price_table(days: dict[date, np.ndarray]) -> pd.DataFrame:
    rows = [
        {"settlement_date": d, "settlement_period": i + 1, "price": float(p)}
        for d, values in days.items()
        for i, p in enumerate(values)
    ]
    return pd.DataFrame(rows)


def test_claim_uses_only_the_day_before_yesterday():
    d = date(2025, 1, 8)
    prices = price_table({d - timedelta(days=2): DAY_SHAPE, d: DAY_SHAPE * 3})
    claim = make_claim(prices, d, SITE, code_version="test")
    assert claim.data_cutoff == uk_midnight(d - timedelta(days=1))  # end of D-2
    assert claim.as_of < claim.target_start
    assert claim.input_data_hash == fingerprint(pd.Series(DAY_SHAPE))


def test_clock_change_days_are_skipped_not_forced():
    d = date(2025, 3, 30)  # spring clock change: 46 periods
    prices = price_table({d - timedelta(days=2): DAY_SHAPE, d: DAY_SHAPE[:46]})
    assert make_claim(prices, d, SITE, code_version="test") is None


def test_perfect_forecast_is_covered_and_pays_only_the_width(tmp_path):
    # when every day looks the same, the plan earns exactly what it claimed
    start = date(2025, 1, 1)
    days = {start + timedelta(days=i): DAY_SHAPE for i in range(7)}
    ledger = Ledger(tmp_path / "ledger.duckdb")
    summary = backtest(price_table(days), date(2025, 1, 3), date(2025, 1, 7), SITE, ledger, "t")
    assert summary.claims == 5
    assert summary.coverage == 1.0
    point = 98 * 0.95 * 150 - 98 / 0.95 * 40
    assert summary.mean_interval_score == pytest.approx(2 * 0.4 * point)
    ledger.close()


def test_rerunning_the_backtest_adds_nothing(tmp_path):
    start = date(2025, 1, 1)
    days = {start + timedelta(days=i): DAY_SHAPE * (1 + 0.1 * i) for i in range(5)}
    prices = price_table(days)
    ledger = Ledger(tmp_path / "ledger.duckdb")
    first = backtest(prices, date(2025, 1, 3), date(2025, 1, 5), SITE, ledger, "t")
    second = backtest(prices, date(2025, 1, 3), date(2025, 1, 5), SITE, ledger, "t")
    assert first.coverage == second.coverage
    assert ledger.con.execute("SELECT count(*) FROM claims").fetchone()[0] == 3
    assert ledger.con.execute("SELECT count(*) FROM resolutions").fetchone()[0] == 3
    ledger.close()
