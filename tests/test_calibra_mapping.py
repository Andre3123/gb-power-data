"""A customer's own export, read as it is through calibra.toml."""

import csv
import tomllib
from datetime import date, timedelta

import numpy as np
import pytest

from gb_power_data.calibra import Calibra, Settings
from gb_power_data.calibra.cli import main as cli
from gb_power_data.calibra.mapping import Mapping, MappingError, draft, read_table

MAPPING = """
agent_id = "acme-optimiser"
timezone = "Europe/London"
period = "1D"
target_name = "daily_revenue"
unit = "GBP"

[columns]
target_start = "delivery_day"
as_of = "created_at"
point = "rev_forecast"
lower = "p10"
upper = "p90"
day_type = "regime"

[features]
wind_gw = "wind_forecast_gw"
spread = "da_spread"

[assets]
round_trip_efficiency = { value = 0.87, source = "Megapack datasheet" }
soc_start = { column = "soc_start_pct", scale = 0.01, source = "BMS" }

[outcomes]
realised = "actual_revenue"
"""


def export(folder, days=120, seed=1):
    """What an optimiser team might really export: their names, local times, no ids."""
    rng = np.random.default_rng(seed)
    f_rows, o_rows = [], []
    for i in range(days):
        d = date(2025, 3, 1) + timedelta(days=i)  # crosses the March clock change
        wild = i % 4 == 0
        point = 18_000 + 300 * i % 2_000
        noise = 6_000 if wild else 900
        f_rows.append({
            "delivery_day": d.isoformat(),
            "created_at": f"{d - timedelta(days=1)} 09:30",
            "rev_forecast": f"{point:.0f}",
            "p10": f"{point - 400:.0f}",
            "p90": f"{point + 400:.0f}",
            "regime": "wild" if wild else "calm",
            "wind_forecast_gw": f"{rng.uniform(2, 15):.1f}",
            "da_spread": f"{(140 if wild else 60) + rng.normal(0, 5):.1f}",
            "soc_start_pct": "50",
        })  # fmt: skip
        o_rows.append({"delivery_day": d.isoformat(),
                       "actual_revenue": f"{point + rng.normal(0, noise):.2f}"})  # fmt: skip
    for name, rows in (("forecasts.csv", f_rows), ("actuals.csv", o_rows)):
        with (folder / name).open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    (folder / "calibra.toml").write_text(MAPPING)
    return folder / "forecasts.csv", folder / "actuals.csv", folder / "calibra.toml"


def test_their_columns_become_calibra_forecasts(tmp_path):
    fpath, _, mpath = export(tmp_path, days=40)
    fs = Mapping.load(mpath).forecasts(read_table(fpath))
    f = fs[0]
    assert f.agent_id == "acme-optimiser" and f.forecast_id == "2025-03-01T00-00Z"
    assert f.features["spread"] > 0 and f.asset["soc_start"] == 0.5
    assert f.asset_source["round_trip_efficiency"] == "Megapack datasheet"
    assert f.as_of.utcoffset() == timedelta(0)  # 1 March 09:30 London is GMT
    summer = next(x for x in fs if x.target_start.date() == date(2025, 4, 1))
    assert summer.target_start.utcoffset() == timedelta(hours=1)  # BST after the change


def test_replay_their_files_end_to_end(tmp_path, capsys):
    fpath, apath, mpath = export(tmp_path)
    ledger = str(tmp_path / "l.duckdb")
    args = ["--ledger", ledger, "replay", "--map", str(mpath), str(fpath), str(apath)]
    assert cli(args) == 0
    rep = Calibra(ledger, Settings()).report("acme-optimiser")
    assert rep["resolved"] == 120
    assert rep["calibra_range"]["coverage"] > 0.8
    assert rep["agent_own_range"]["coverage"] < 0.6  # their p10-p90 is far too narrow


def test_a_bad_mapping_lists_every_problem(tmp_path):
    with pytest.raises(MappingError) as e:
        Mapping.from_dict({"columns": {"point": "x", "wrong": "y"}, "timezone": "Mars/Base"})
    text = str(e.value)
    for bit in ("agent_id is missing", "wrong: not a Calibra field", "target_start is required",
                "period", "as_of is required", "Mars/Base"):  # fmt: skip
        assert bit in text


def test_a_missing_column_is_named(tmp_path):
    fpath, _, mpath = export(tmp_path, days=5)
    m = Mapping.load(mpath)
    m.features["temp"] = "air_temperature"
    with pytest.raises(MappingError, match="'air_temperature' is not in the file"):
        m.forecasts(read_table(fpath))


def test_init_drafts_a_mapping_that_loads(tmp_path):
    fpath, _, _ = export(tmp_path, days=5)
    text = draft(read_table(fpath), "acme")
    cfg = tomllib.loads(text)
    assert cfg["columns"]["target_start"] == "delivery_day"
    assert cfg["columns"]["as_of"] == "created_at"
    assert cfg["columns"]["point"] == "rev_forecast"
    assert cfg["columns"]["lower"] == "p10" and cfg["columns"]["upper"] == "p90"
    assert "wind_forecast_gw" in cfg["features"].values()
    assert "CHECK" in text


def test_guess_handles_unit_suffixes_and_common_names():
    from gb_power_data.calibra.mapping import guess

    rows = [
        {
            "target_start": "2024-01-01 17:00",
            "made_at": "2023-12-31 09:00",
            "nd_forecast_mw": "30000",
            "nd_p05_mw": "28000",
            "nd_p95_mw": "32000",
            "temperature_c": "5",
        }
    ]
    cols = guess(rows)["columns"]
    assert cols["point"] == "nd_forecast_mw"
    assert (cols["lower"], cols["upper"]) == ("nd_p05_mw", "nd_p95_mw")
    assert (cols["as_of"], cols["target_start"]) == ("made_at", "target_start")
    rows = [
        {
            "timestamp": "2024-01-01",
            "issued_at": "2023-12-31",
            "predicted": "3",
            "q05": "1",
            "q95": "5",
        }
    ]
    cols = guess(rows)["columns"]
    assert (cols["point"], cols["lower"], cols["upper"]) == ("predicted", "q05", "q95")
    assert (cols["as_of"], cols["target_start"]) == ("issued_at", "timestamp")
