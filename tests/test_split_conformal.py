from datetime import date, timedelta

import numpy as np
import pytest
from test_dayahead import DAY_SHAPE, SITE, price_table

from gb_power_data.calibration.split_conformal import conformal_half_width, residuals_from_ledger
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