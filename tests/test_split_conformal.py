from datetime import date, timedelta

import numpy as np
import pytest
from test_dayahead import DAY_SHAPE, SITE, price_table

from gb_power_data.calibration.split_conformal import (
    conformal_half_width,
    mondrian_half_widths,
    residuals_by_class,
    residuals_from_ledger,
)
from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger


def test_half_width_is_the_finite_sample_quantile_of_the_errors():
    errors = np.array([-9, 1, -2, 3, 4, -5, 6, 7, 8], dtype=float)  # sizes 1..9
    # n = 9: 90% needs the ceil(10 * 0.9) = 9th smallest size; 50% needs the 5th
    assert conformal_half_width(errors, 0.90) == 9
    assert conformal_half_width(errors, 0.50) == 5


def test_refuses_when_there_are_too_few_past_errors():
    with pytest.raises(ValueError, match="at least 9"):
        conformal_half_width(np.array([1.0, 2.0, 3.0]), 0.90)


def test_learned_width_is_used_and_errors_come_from_one_agent_only(tmp_path):
    start = date(2025, 1, 1)
    days = {start + timedelta(days=i): DAY_SHAPE * (1 + 0.2 * (i % 3)) for i in range(8)}
    prices = price_table(days)
    ledger = Ledger(tmp_path / "ledger.duckdb")
    backtest(prices, date(2025, 1, 3), date(2025, 1, 5), SITE, ledger, "t")
    backtest(prices, date(2025, 1, 6), date(2025, 1, 8), SITE, ledger, "t", "persistence-cp", 500.0)

    claim = ledger.get_claim("pillswood-persistence-cp-2025-01-06")
    assert claim.upper - claim.lower == pytest.approx(1000.0)
    errors = residuals_from_ledger(
        ledger, "persistence-dispatch", date(2025, 1, 1), date(2025, 1, 31)
    )
    assert len(errors) == 3  # only the naive agent's three claims, not the conformal ones
    ledger.close()

def test_mondrian_learns_one_width_per_day_type():
    by_class = {
        "calm": np.arange(1, 10, dtype=float),  # sizes 1..9
        "wild": np.arange(10, 100, 10, dtype=float),  # sizes 10..90
    }
    # each type: n = 9, 90% needs its own 9th smallest size
    assert mondrian_half_widths(by_class, 0.90) == {"calm": 9.0, "wild": 90.0}


def test_mondrian_names_the_day_type_that_lacks_history():
    by_class = {"calm": np.arange(1, 10, dtype=float), "wild": np.array([5.0, 6.0])}
    with pytest.raises(ValueError, match="class 'wild': need at least 9"):
        mondrian_half_widths(by_class, 0.90)


def test_each_claim_gets_the_width_of_its_own_day_type(tmp_path):
    # spread 44 is calm (< 52.8), spread 110 is wild (> 82.3); the class comes from D-2
    start = date(2025, 1, 1)
    days = {start + timedelta(days=i): DAY_SHAPE * (0.4 if i % 2 else 1.0) for i in range(8)}
    prices = price_table(days)
    ledger = Ledger(tmp_path / "ledger.duckdb")
    widths = {"calm": 100.0, "normal": 200.0, "wild": 300.0}
    backtest(
        prices, date(2025, 1, 3), date(2025, 1, 6), SITE, ledger, "t", "persistence-mcp", widths
    )

    from_wild = ledger.get_claim("pillswood-persistence-mcp-2025-01-03")  # D-2 = 1 Jan, wild
    from_calm = ledger.get_claim("pillswood-persistence-mcp-2025-01-04")  # D-2 = 2 Jan, calm
    assert (from_wild.reference_class, from_wild.upper - from_wild.lower) == ("wild", 600.0)
    assert (from_calm.reference_class, from_calm.upper - from_calm.lower) == ("calm", 200.0)
    groups = residuals_by_class(ledger, "persistence-mcp-dispatch", start, date(2025, 1, 31))
    assert set(groups) == {"calm", "wild"}  # errors are filed under their day type