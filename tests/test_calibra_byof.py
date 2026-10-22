"""Calibra for any forecaster: a made-up optimiser with an over-confident range of its own."""

import asyncio
import csv
from datetime import UTC, datetime, timedelta

import duckdb
import numpy as np
import pytest
from mcp import Client
from pydantic import ValidationError

from gb_power_data.calibra import Calibra, Forecast, Outcome, Settings
from gb_power_data.calibra.cli import main as cli
from gb_power_data.calibra.mcp import build_server
from gb_power_data.ledger.store import Ledger

T0 = datetime(2025, 1, 1, tzinfo=UTC)
NOISE = {"calm": 1_000.0, "wild": 8_000.0}  # true error size differs by day type


def forecaster(n: int = 240, seed: int = 0):
    """Daily forecasts made the day before, outcomes = point + noise; own range far too narrow."""
    rng = np.random.default_rng(seed)
    forecasts, outcomes = [], []
    for i in range(n):
        start = T0 + timedelta(days=i)
        kind = "wild" if i % 3 == 0 else "calm"
        point = 20_000.0 + 500 * np.sin(i / 7)
        spread = 60.0 if kind == "calm" else 140.0
        forecasts.append(
            Forecast(
                forecast_id=f"d{i:03d}",
                agent_id="acme",
                target_start=start,
                target_end=start + timedelta(days=1),
                as_of=start - timedelta(days=1),
                point=point,
                lower=point - 0.3 * NOISE[kind],
                upper=point + 0.3 * NOISE[kind],
                day_type=kind,
                features={"spread": spread + rng.normal(0, 5)},
                asset={"round_trip_efficiency": 0.87},
                asset_source={"round_trip_efficiency": "datasheet"},
                target_name="daily_revenue",
                unit="GBP",
            )
        )
        outcomes.append(
            Outcome(
                forecast_id=f"d{i:03d}",
                agent_id="acme",
                realised=point + rng.normal(0, NOISE[kind]),
            )
        )
    return forecasts, outcomes


@pytest.fixture
def engine(tmp_path):
    return Calibra(tmp_path / "ledger.duckdb", Settings(code_version="test"))


def test_forecast_rules_are_enforced():
    base = dict(forecast_id="x", agent_id="a", point=1.0, target_start=T0,
                target_end=T0 + timedelta(days=1), as_of=T0 - timedelta(hours=1))  # fmt: skip
    with pytest.raises(ValidationError, match="timezone"):
        Forecast(**{**base, "as_of": datetime(2024, 12, 31)})  # noqa: DTZ001
    with pytest.raises(ValidationError, match="beforehand"):
        Forecast(**{**base, "as_of": T0 + timedelta(hours=1)})
    with pytest.raises(ValidationError, match="both lower and upper"):
        Forecast(**{**base, "lower": 0.5})
    with pytest.raises(ValidationError, match="pattern"):
        Forecast(**{**base, "forecast_id": "has space"})


def test_calibra_range_is_honest_where_the_agents_own_is_not(engine):
    forecasts, outcomes = forecaster()
    result = engine.replay(forecasts, outcomes)
    assert result["submitted"] == result["resolved"] == 240
    rep = engine.report("acme")
    assert 0.84 <= rep["calibra_range"]["coverage"] <= 0.96  # promised 0.90
    assert rep["agent_own_range"]["coverage"] < 0.35  # the agent's own +-0.3 sigma range
    assert rep["methods"]["mondrian"] > 150  # per day type once each type has 20 outcomes
    assert "none" in rep["methods"]  # the first forecasts have no history to learn from


def test_wild_days_get_wider_ranges_than_calm_days(engine):
    engine.replay(*forecaster())
    df = engine.frame("acme")
    late = df[df["method"] == "mondrian"]
    width = (late["upper"] - late["lower"]).groupby(late["day_type"]).mean()
    assert width["wild"] > 4 * width["calm"]


def test_an_outcome_not_yet_known_is_never_used(engine):
    f, o = forecaster(n=30)
    for i in range(25):
        engine.submit(f[i])
        engine.resolve(o[i])
    # a forecast made before most of those targets ended may only learn from the ones that had
    early = f[25].model_copy(update={"as_of": f[5].target_end, "forecast_id": "late-made-early"})
    r = engine.submit(early)
    assert engine.calibration("acme", early.as_of).past_outcomes == 6  # targets 0..5 ended
    assert r["method"] == "none"  # 6 outcomes are too few for a 90% range


def test_scope_flags_inputs_unlike_the_history(engine):
    f, o = forecaster(n=60)
    engine.replay(f, o)
    late = T0 + timedelta(days=60)
    assert engine.check_scope("acme", late, {"spread": 100.0})["in_scope"] is True
    out = engine.check_scope("acme", late, {"spread": 900.0, "wind": 3.0})
    assert not out["in_scope"]
    assert any(x.startswith("spread 900 above") for x in out["flags"])
    assert any(x.startswith("wind has too little history") for x in out["flags"])


def test_a_forecast_is_written_once(engine):
    f, _ = forecaster(n=1)
    assert engine.submit(f[0])["new"] is True
    assert engine.submit(f[0])["new"] is False
    with pytest.raises(ValueError, match="different content"):
        engine.submit(f[0].model_copy(update={"point": 1.0}))


def test_editing_a_recorded_forecast_breaks_the_chain(engine):
    engine.replay(*forecaster(n=40))
    con = duckdb.connect(str(engine.ledger_path))
    con.execute("UPDATE claims SET upper = upper + 1 WHERE claim_id = 'acme:d030'")
    con.close()
    led = Ledger(engine.ledger_path)
    assert not led.verify().ok
    led.close()


def _write(path, rows):
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)


def test_cli_template_replay_report(tmp_path, capsys):
    ledger = str(tmp_path / "l.duckdb")
    assert cli(["--ledger", ledger, "template", str(tmp_path)]) == 0
    files = [str(tmp_path / "forecasts.csv"), str(tmp_path / "outcomes.csv")]
    assert cli(["--ledger", ledger, "replay", *files]) == 0
    assert cli(["--ledger", ledger, "report", "my-optimiser"]) == 0
    assert cli(["--ledger", ledger, "verify"]) == 0
    assert '"resolved": 1' in capsys.readouterr().out


def test_cli_rejects_a_bad_file_and_writes_nothing(tmp_path, capsys):
    bad = tmp_path / "f.csv"
    _write(bad, [["forecast_id", "agent_id", "target_start", "target_end", "as_of", "point"],
                 ["a1", "acme", "2025-01-02T00:00:00", "2025-01-03T00:00:00+00:00",
                  "2025-01-01T00:00:00+00:00", "10"],
                 ["a2", "acme", "2025-01-02T00:00:00+00:00", "2025-01-03T00:00:00+00:00",
                  "2025-01-01T00:00:00+00:00", "ten"]])  # fmt: skip
    ledger = tmp_path / "l.duckdb"
    assert cli(["--ledger", str(ledger), "submit", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "line 2" in err and "line 3" in err and "nothing was written" in err
    assert not ledger.exists()


def test_mcp_gives_agents_no_way_to_record_outcomes(engine):
    engine.replay(*forecaster(n=40))
    start = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async def talk():
        async with Client(build_server(engine)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            r = await client.call_tool(
                "submit_forecast",
                {
                    "agent_id": "acme",
                    "forecast_id": "mcp-1",
                    "target_start": start.isoformat(),
                    "target_end": (start + timedelta(days=1)).isoformat(),
                    "point": 20000,
                    "day_type": "calm",
                    "features": {"spread": 61},
                },
            )
            return names, r.structured_content

    names, receipt = asyncio.run(talk())
    assert names == {"check_scope", "submit_forecast", "calibration_status", "agent_report"}
    assert receipt["new"] is True and receipt["method"] == "mondrian"  # 26 calm outcomes
    assert receipt["live"] is True  # recorded before its target period


def test_mcp_tells_the_agent_why_a_forecast_was_refused(engine):
    async def talk():
        async with Client(build_server(engine)) as client:
            return await client.call_tool(
                "submit_forecast",
                {
                    "agent_id": "acme",
                    "forecast_id": "late",
                    "target_start": "2025-01-02T00:00:00+00:00",
                    "target_end": "2025-01-03T00:00:00+00:00",
                    "point": 1.0,
                },
            )

    r = asyncio.run(talk())
    assert r.is_error
    assert "must be made beforehand" in r.content[0].text  # as_of defaults to now
