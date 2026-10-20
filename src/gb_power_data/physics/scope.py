"""Physical scope: what a claim assumes about the battery, and when its plan leans on a limit.

ScopeGate asks "are today's prices like the ones we learned on?". This module asks the
physical question: "which battery numbers does this plan rest on, how sure are we of each, and
does the plan push the battery to a limit where those numbers matter most?".
"""

from __future__ import annotations

import pandas as pd

from gb_power_data.optimise.dispatch import Site

# Where each Pillswood number comes from. ACCURE 2026 = "Banking BESS" field-data report.
SOURCES = {
    "power_mw": "verified: data/sites/pillswood.toml",
    "energy_mwh": "verified nameplate; live usable energy runs a median 6.9% lower (ACCURE 2026)",
    "eta_charge": "placeholder 0.95; datasheet gives 0.933 (Tesla Megapack, 87% round trip)",
    "eta_discharge": "placeholder 0.95; datasheet gives 0.933 (Tesla Megapack, 87% round trip)",
    "degradation_gbp_per_mwh": "placeholder; no public figure, heuristic range 5-30",
    "max_cycles_per_day": "placeholder; warranty-style cap, site value not found",
    "soc_start": "assumption; reported SoC is a median 5.6 points off measured (ACCURE 2026)",
    "soc_min": "unverified: operating window not found",
    "soc_max": "unverified: operating window not found",
}


def assumptions(site: Site, sources: dict[str, str] = SOURCES) -> dict[str, dict]:
    """Each physical number the plan used, with where it comes from."""
    return {
        name: {"value": getattr(site, name), "source": source} for name, source in sources.items()
    }


def physical_flags(schedule: pd.DataFrame, site: Site, tol: float = 0.01) -> list[str]:
    """Reasons the plan leans on a physical limit; an empty list means none.

    Cycle cap: if the plan discharges the full daily allowance, the cap is binding. The plan
    then rests on the cap and the wear cost, the two least certain numbers we hold.
    """
    flags = []
    if site.max_cycles_per_day is not None and site.max_cycles_per_day > 0:
        cycles = float(schedule["discharge_mw"].sum() * site.dt_h) / site.energy_mwh
        if cycles >= site.max_cycles_per_day * (1 - tol):
            flags.append(
                f"plan uses the full cycle cap ({cycles:.2f} of {site.max_cycles_per_day} "
                "cycles): the cap and the wear cost are placeholders"
            )
    return flags