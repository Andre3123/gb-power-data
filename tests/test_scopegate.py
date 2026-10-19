from datetime import date, timedelta

import pytest
from test_dayahead import DAY_SHAPE, SITE, price_table

from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger
from gb_power_data.scopegate.gate import daily_stats, learn_gate

START = date(2025, 1, 1)


def history(n_days: int, spike_on: date | None = None):
    """Ordinary days with small variation; optionally one day with a 10x price spike."""
    days = {}
    for i in range(n_days):
        d = START + timedelta(days=i)
        shape = DAY_SHAPE * (1 + 0.05 * (i % 5))
        days[d] = shape * 10 if d == spike_on else shape
    return price_table(days)


def test_daily_stats_measure_each_day():
    stats = daily_stats(history(10))
    first = stats.loc[START]
    assert (first["max_price"], first["min_price"], first["spread"]) == (150.0, 40.0, 110.0)
    assert stats["volatility_7d"].isna().sum() == 4  # needs at least 5 days of history


def test_ordinary_day_is_in_scope_and_a_spike_is_flagged():
    spike = START + timedelta(days=25)
    gate = learn_gate(history(30, spike_on=spike), START, START + timedelta(days=19))
    assert gate.flags(START + timedelta(days=21)) == []  # D-2 = day 19, like the learning days
    reasons = gate.flags(spike + timedelta(days=2))  # D-2 is the spike day
    assert any(r.startswith("spread") for r in reasons)
    assert any(r.startswith("max_price") for r in reasons)


def test_missing_history_is_flagged_not_trusted():
    gate = learn_gate(history(30), START, START + timedelta(days=19))
    assert gate.flags(START + timedelta(days=60)) == ["no complete price history up to D-2"]


def test_backtest_records_flags_and_splits_results_by_scope(tmp_path):
    spike = START + timedelta(days=25)
    prices = history(30, spike_on=spike)
    gate = learn_gate(prices, START, START + timedelta(days=19))
    ledger = Ledger(tmp_path / "ledger.duckdb")
    first, last = START + timedelta(days=23), START + timedelta(days=29)
    summary = backtest(prices, first, last, SITE, ledger, "t", "gated", 500.0, gate=gate)

    flagged = ledger.get_claim(f"pillswood-gated-{spike + timedelta(days=2)}")
    assert flagged.context["in_scope"] is False
    assert flagged.context["scope_flags"]
    assert ledger.get_claim(f"pillswood-gated-{first}").context["in_scope"] is True
    assert set(summary.by_scope.index) == {True, False}
    assert summary.by_scope["share_of_bill"].sum() == pytest.approx(1.0)
    ledger.close()
