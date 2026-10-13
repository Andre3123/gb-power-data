import numpy as np
import pandas as pd
import pytest

from gb_power_data.optimise.dispatch import Site, shadow_prices, solve_day

SITE = Site(power_mw=98, energy_mwh=196, eta_charge=0.95, eta_discharge=0.95)


def step_prices() -> pd.Series:
    """8 hours at 40, 8 hours at 60, 8 hours at 150 (48 half-hours)."""
    return pd.Series(np.r_[np.full(16, 40.0), np.full(16, 60.0), np.full(16, 150.0)])


def test_energy_balance_holds_every_period():
    s = solve_day(step_prices(), SITE).schedule
    prev = np.r_[SITE.soc_start * SITE.energy_mwh, s.energy_mwh.to_numpy()[:-1]]
    expected = (
        prev
        + SITE.eta_charge * SITE.dt_h * s.charge_mw
        - SITE.dt_h / SITE.eta_discharge * s.discharge_mw
    )
    assert np.allclose(s.energy_mwh, expected, atol=1e-6)


def test_limits_are_respected():
    s = solve_day(step_prices(), SITE).schedule
    assert s.energy_mwh.between(-1e-6, SITE.energy_mwh + 1e-6).all()
    assert (s.charge_mw <= SITE.power_mw + 1e-6).all()
    assert (s.discharge_mw <= SITE.power_mw + 1e-6).all()
    assert s.energy_mwh.iloc[-1] >= SITE.soc_start * SITE.energy_mwh - 1e-6


def test_matches_the_first_principles_answer_on_a_step_day():
    # best plan: fill from half (98 MWh) to full on the cheap block, then sell back
    # down to half on the dear block (the end-of-day rule stops it going lower)
    bought = 98 / SITE.eta_charge * 40
    sold = 98 * SITE.eta_discharge * 150
    assert solve_day(step_prices(), SITE).revenue_gbp == pytest.approx(sold - bought, rel=1e-6)


def test_flat_prices_mean_no_trading():
    # with losses, any cycle at a flat price loses money, so the best plan is to do nothing
    result = solve_day(pd.Series(np.full(48, 50.0)), SITE)
    assert result.revenue_gbp == pytest.approx(0, abs=1e-6)


def test_never_charges_and_discharges_at_once_even_at_negative_prices():
    # without the on/off variable, an LP 'burns' energy by doing both when paid to consume
    prices = pd.Series(np.r_[np.full(24, -100.0), np.full(24, 30.0)])
    assert solve_day(prices, SITE).simultaneous_periods == 0


def test_wear_cost_above_the_spread_stops_trading():
    # the step day earns at most about 150 * 0.95 - 40 / 0.95 = 100.4 GBP per MWh cycled
    worn = Site(**{**SITE.__dict__, "degradation_gbp_per_mwh": 200.0})
    assert solve_day(step_prices(), worn).revenue_gbp == pytest.approx(0, abs=1e-6)


def test_cycle_cap_limits_daily_throughput():
    capped = Site(**{**SITE.__dict__, "max_cycles_per_day": 0.25})
    s = solve_day(step_prices(), capped).schedule
    assert (s.discharge_mw * SITE.dt_h).sum() <= 0.25 * SITE.energy_mwh + 1e-6


@pytest.mark.parametrize(
    "bad",
    [
        {"eta_charge": 1.2},
        {"eta_discharge": 0},
        {"soc_start": 0.9, "soc_max": 0.8},
        {"power_mw": 0},
    ],
)
def test_rejects_impossible_site_parameters(bad):
    with pytest.raises(ValueError):
        Site(**{**SITE.__dict__, **bad})


def test_rejects_prices_with_gaps():
    prices = step_prices()
    prices.iloc[5] = np.nan
    with pytest.raises(ValueError, match="no gaps"):
        solve_day(prices, SITE)

def test_shadow_price_is_the_cost_then_the_value_of_a_stored_mwh():
    # while filling at GBP 40, one stored MWh cost 40 / 0.95; once full and waiting to sell
    # at GBP 150, one stored MWh is worth 150 * 0.95 (Lagrangian of the storage problem)
    prices = step_prices()
    lam = shadow_prices(prices, SITE, solve_day(prices, SITE))
    assert lam.iloc[0] == pytest.approx(40 / SITE.eta_charge, rel=1e-6)
    assert lam.iloc[-1] == pytest.approx(150 * SITE.eta_discharge, rel=1e-6)
