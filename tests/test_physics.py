from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from test_dayahead import DAY_SHAPE, SITE

from gb_power_data.physics.sensitivity import (
    EFFICIENCY,
    ENERGY_FACTOR,
    SOC_OFFSET,
    WEAR_GBP_PER_MWH,
    physics_cases,
    physics_spread,
)

# one cheap block, one dear block, twice a day: room for more than one cycle
TWO_PEAKS = pd.Series(np.r_[DAY_SHAPE[:24], DAY_SHAPE[:24]] * 2, index=range(1, 49))
BASE = replace(SITE, eta_charge=0.95, eta_discharge=0.95, degradation_gbp_per_mwh=15.0)


def test_every_case_is_replanned():
    cases = physics_cases(TWO_PEAKS, BASE)
    grid = len(EFFICIENCY) * len(WEAR_GBP_PER_MWH)
    assert len(cases) == grid + len(ENERGY_FACTOR) + len(SOC_OFFSET)
    for c in cases:
        assert c.net_revenue_gbp == pytest.approx(c.market_revenue_gbp - c.wear_cost_gbp)


def test_physics_moves_the_plan_the_way_physics_should():
    cases = physics_cases(TWO_PEAKS, BASE)
    grid = {
        (c.efficiency, c.wear_gbp_per_mwh): c
        for c in cases
        if c.energy_factor == 1.0 and c.soc_start == BASE.soc_start
    }
    for eta in EFFICIENCY:  # dearer wear never makes the plan cycle more
        cycles = [grid[(eta, w)].cycles for w in WEAR_GBP_PER_MWH]
        assert cycles == sorted(cycles, reverse=True)
    for w in WEAR_GBP_PER_MWH:  # a more efficient battery never earns less
        assert grid[(0.95, w)].net_revenue_gbp >= grid[(0.90, w)].net_revenue_gbp - 1e-6
    full = grid[(0.95, 15.0)].net_revenue_gbp  # less usable energy never earns more
    for c in cases:
        if c.energy_factor < 1.0:
            assert c.net_revenue_gbp <= full + 1e-6


def test_spread_reports_each_effect_and_is_zero_on_a_flat_day():
    spread = physics_spread(physics_cases(TWO_PEAKS, BASE), BASE)
    for key in ("wear_effect_gbp", "efficiency_effect_gbp", "energy_effect_gbp"):
        assert spread[key] > 0
    assert spread["spread_gbp"] >= max(spread["wear_effect_gbp"], spread["energy_effect_gbp"])
    flat = pd.Series(np.full(48, 50.0), index=range(1, 49))  # no spread: nothing to trade
    still = physics_spread(physics_cases(flat, BASE), BASE)
    assert still["spread_gbp"] == pytest.approx(0, abs=1e-6)
