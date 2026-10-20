from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from gb_power_data.claims.dayahead import make_claim
from gb_power_data.optimise.dispatch import Site, solve_day
from gb_power_data.physics.scope import SOURCES, assumptions, physical_flags

SITE = Site(power_mw=98, energy_mwh=196, eta_charge=0.95, eta_discharge=0.95)
STEP = pd.Series(np.r_[np.full(16, 40.0), np.full(16, 60.0), np.full(16, 150.0)])


def test_every_assumption_has_its_value_and_source():
    a = assumptions(SITE)
    assert set(a) == set(SOURCES)
    assert a["eta_charge"]["value"] == 0.95
    assert "placeholder" in a["eta_charge"]["source"]


def test_flag_fires_only_when_the_cycle_cap_binds():
    tight = replace(SITE, max_cycles_per_day=0.25)  # far less than the step day wants
    loose = replace(SITE, max_cycles_per_day=5.0)  # never reached
    assert "full cycle cap" in physical_flags(solve_day(STEP, tight).schedule, tight)[0]
    assert physical_flags(solve_day(STEP, loose).schedule, loose) == []
    assert physical_flags(solve_day(STEP, SITE).schedule, SITE) == []  # no cap set


def test_claim_records_physics_and_flags():
    d = date(2025, 1, 8)
    rows = [
        {"settlement_date": day, "settlement_period": i + 1, "price": float(p)}
        for day in (d - timedelta(days=2), d)
        for i, p in enumerate(STEP)
    ]
    tight = replace(SITE, max_cycles_per_day=0.25)
    claim = make_claim(pd.DataFrame(rows), d, tight, code_version="test")
    assert claim.context["physics"]["max_cycles_per_day"]["value"] == 0.25
    assert len(claim.context["physical_flags"]) == 1
    assert claim.context["in_scope"]  # physical flags do not change the market scope