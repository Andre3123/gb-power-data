import numpy as np
import pandas as pd
import pytest

from gb_power_data.crosscheck.cli import compare
from gb_power_data.crosscheck.pypsa_check import pypsa_revenue, solve_day_pypsa
from gb_power_data.optimise.dispatch import solve_day
from gb_power_data.optimise.run_day import PILLSWOOD


def step_day() -> pd.Series:
    """8 hours at 40, 8 hours at 60, 8 hours at 150 (48 half-hours)."""
    return pd.Series(np.r_[np.full(16, 40.0), np.full(16, 60.0), np.full(16, 150.0)])


def negative_day() -> pd.Series:
    """Windy night with negative prices, then a dear evening peak."""
    return pd.Series(np.r_[np.full(16, -20.0), np.full(16, 30.0), np.full(16, 120.0)])


def three_peak_day() -> pd.Series:
    """Three cheap/dear swings, more cycling than the 1.5 cycles/day cap allows."""
    block = np.r_[np.full(8, 20.0), np.full(8, 140.0)]
    return pd.Series(np.tile(block, 3))


@pytest.mark.parametrize("prices", [step_day(), negative_day(), three_peak_day()])
def test_pypsa_agrees_with_our_optimiser_within_1pct(prices):
    ours = solve_day(prices, PILLSWOOD).revenue_gbp
    theirs = pypsa_revenue(prices, PILLSWOOD)
    assert theirs == pytest.approx(ours, rel=0.01)


def test_step_day_matches_first_principles():
    # fill from half to full at 40, sell back down to half at 150, wear does not change the plan
    bought = 98 / PILLSWOOD.eta_charge * 40
    sold = 98 * PILLSWOOD.eta_discharge * 150
    assert pypsa_revenue(step_day(), PILLSWOOD) == pytest.approx(sold - bought, rel=1e-6)


def test_pypsa_respects_cycle_cap_and_end_energy():
    s = solve_day_pypsa(three_peak_day(), PILLSWOOD).schedule
    discharged = s.discharge_mw.sum() * PILLSWOOD.dt_h
    assert discharged <= PILLSWOOD.max_cycles_per_day * PILLSWOOD.energy_mwh + 1e-6
    assert s.energy_mwh.iloc[-1] >= PILLSWOOD.soc_start * PILLSWOOD.energy_mwh - 1e-6


def test_compare_reports_one_row_per_day():
    rows = []
    for day, prices in [("2025-01-01", step_day()), ("2025-01-02", negative_day())]:
        rows.append(
            pd.DataFrame(
                {"settlement_date": day, "settlement_period": range(1, 49), "price": prices}
            )
        )
    res = compare(
        pd.concat(rows), pd.Timestamp("2025-01-01").date(), pd.Timestamp("2025-01-02").date()
    )
    assert len(res) == 2
    assert (res.gap_pct.abs() < 1).all()
