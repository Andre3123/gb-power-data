"""A stand-in customer for Calibra: OpenSTEF day-ahead forecasts, exported as plain CSV files.

OpenSTEF (Alliander, LF Energy) is a real forecasting package. Here it plays "a customer's own
forecaster". It runs in its own Python environment and Calibra never imports it: the only thing
that crosses over is two CSV files with OpenSTEF-style column names.

What is forecast: GB National Demand (ND, from NESO's historic demand files) at 17:00 UTC, the
evening peak, one forecast per delivery day. Each forecast is made at 09:00 UTC the day before
(day-ahead) from data known at that time only, and carries OpenSTEF's own 5% to 95% range.
The model is retrained every few days on past data only, so there is no look-ahead.

Honest limits: load only (no weather inputs), national demand rather than a substation, and a
model that was not tuned. It is a stand-in customer, not a real client.

Usage (see README.md):
    uv run make_export.py --csv demand_2023.csv demand_2024.csv demand_2025.csv --out export
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SAMPLE = timedelta(hours=1)
QUANTILES = (0.05, 0.5, 0.95)


DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y")  # NESO changed style between years


def parse_days(values: pd.Series, path: Path) -> pd.Series:
    """Read one file's dates with the first format that reads every row. Never guesses."""
    text = values.astype(str).str.strip().str.strip('"')
    for fmt in DATE_FORMATS:
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if parsed.notna().all():
            return parsed
    sys.exit(f"{path}: dates such as {text.iloc[0]!r} match none of {DATE_FORMATS}")


def check_load(load: pd.Series) -> None:
    """Stop early if the data cannot be GB national demand."""
    missing = int(load.isna().sum())
    if load.min() < 10_000 or load.max() > 60_000:
        sys.exit(f"load range {load.min():.0f} to {load.max():.0f} MW is not plausible for ND")
    if missing > 0.01 * len(load):
        sys.exit(f"{missing} of {len(load)} hours are missing: check the files and their dates")
    print(f"load check ok: {load.min():.0f} to {load.max():.0f} MW, {missing} missing hours")


def read_neso(paths: list[Path], load_col: str = "ND") -> pd.Series:
    """NESO historic demand files -> hourly mean load in MW, indexed by UTC time.

    NESO files have one row per half hour: SETTLEMENT_DATE, SETTLEMENT_PERIOD (1 to 48, from
    local midnight; 46 or 50 on clock-change days) and the demand columns, ND among them.
    """
    frames = []
    for path in paths:
        df = pd.read_csv(path)
        df.columns = [c.strip().upper() for c in df.columns]
        need = {"SETTLEMENT_DATE", "SETTLEMENT_PERIOD", load_col.upper()}
        if not need <= set(df.columns):
            sys.exit(f"{path}: expected columns {sorted(need)}; found {list(df.columns)[:12]}")
        df["SETTLEMENT_DATE"] = parse_days(df["SETTLEMENT_DATE"], path)
        frames.append(df[["SETTLEMENT_DATE", "SETTLEMENT_PERIOD", load_col.upper()]])
    df = pd.concat(frames, ignore_index=True)
    day = df["SETTLEMENT_DATE"]
    midnight = day.dt.tz_localize("Europe/London").dt.tz_convert("UTC")
    start = midnight + pd.to_timedelta((df["SETTLEMENT_PERIOD"].astype(int) - 1) * 30, unit="min")
    half_hourly = pd.Series(df[load_col.upper()].astype(float).to_numpy(), index=start)
    half_hourly = half_hourly[~half_hourly.index.duplicated(keep="last")].sort_index()
    hourly = half_hourly.resample("1h").mean()
    hourly.name = "load"
    return hourly


def workflow():
    """OpenSTEF's own forecasting workflow, quantiles 5%, 50%, 95%, no experiment tracking."""
    from openstef_core.types import LeadTime, Q
    from openstef_models.presets import ForecastingWorkflowConfig, create_forecasting_workflow

    config = ForecastingWorkflowConfig(
        model_id="calibra-standin",
        model="xgboost",
        quantiles=[Q(q) for q in QUANTILES],
        sample_interval=SAMPLE,
        horizons=[LeadTime.from_string("PT48H")],
        mlflow_storage=None,
        model_reuse_enable=False,
        model_selection_enable=False,
        verbosity=0,
    )
    return create_forecasting_workflow(config)


def day_class(day: pd.Timestamp) -> str:
    return ("weekday", "weekday", "weekday", "weekday", "weekday", "saturday", "sunday")[
        day.dayofweek
    ]


def export(
    load: pd.Series,
    first_day: str,
    last_day: str,
    out: Path,
    train_days: int,
    refit_days: int,
    issue_hour: int,
    target_hour: int,
) -> dict:
    from openstef_core.datasets import TimeSeriesDataset

    full = load.reindex(pd.date_range(load.index.min(), load.index.max(), freq="1h"))
    days = pd.date_range(first_day, last_day, freq="D", tz="UTC")
    model, last_fit = None, None
    forecasts, outcomes, crossings = [], [], 0
    for day in days:
        issued = day - timedelta(days=1) + timedelta(hours=issue_hour)  # the forecast is made now
        target = day + timedelta(hours=target_hour)
        if model is None or (day - last_fit).days >= refit_days:
            past = full[(full.index < issued) & (full.index >= issued - timedelta(days=train_days))]
            model = workflow()
            model.fit(TimeSeriesDataset(past.to_frame("load"), sample_interval=SAMPLE))
            last_fit = day
        known = full[(full.index < issued) & (full.index >= issued - timedelta(days=14))]
        ahead = pd.Series(np.nan, index=pd.date_range(issued, day + timedelta(hours=23), freq="1h"))
        window = pd.concat([known, ahead]).to_frame("load")
        window = window[~window.index.duplicated(keep="first")]
        result = model.predict(
            TimeSeriesDataset(window, sample_interval=SAMPLE), forecast_start=issued.to_pydatetime()
        ).data
        actual = full.get(target, np.nan)
        if target not in result.index or np.isnan(actual):
            continue  # no forecast or no real value for that day: it is not scored
        q05, q50, q95 = (
            float(result.loc[target, f"quantile_P{int(q * 100):02d}"]) for q in QUANTILES
        )
        if not q05 <= q50 <= q95:
            crossings += 1
            q05, q50, q95 = sorted((q05, q50, q95))
        forecasts.append(
            {
                "delivery_day": day.strftime("%Y-%m-%d"),
                "created_at": issued.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "nd_forecast_mw": round(q50, 1),
                "nd_p05_mw": round(q05, 1),
                "nd_p95_mw": round(q95, 1),
                "day_class": day_class(day),
                "nd_last_week_same_time_mw": round(
                    float(full.get(target - timedelta(days=7), np.nan)), 1
                ),
                "nd_latest_mw": round(float(full.get(issued - timedelta(hours=1), np.nan)), 1),
                "model_version": "openstef-xgboost",
            }
        )
        outcomes.append(
            {"delivery_day": day.strftime("%Y-%m-%d"), "nd_actual_mw": round(float(actual), 1)}
        )
    out.mkdir(parents=True, exist_ok=True)
    f = pd.DataFrame(forecasts)
    o = pd.DataFrame(outcomes)
    f.to_csv(out / "forecasts.csv", index=False)
    o.to_csv(out / "outcomes.csv", index=False)
    (out / "calibra.toml").write_text(MAPPING, encoding="utf-8")
    joined = f.merge(o, on="delivery_day")
    inside = (joined["nd_p05_mw"] <= joined["nd_actual_mw"]) & (
        joined["nd_actual_mw"] <= joined["nd_p95_mw"]
    )
    return {
        "days": len(joined),
        "own_range_nominal": 0.90,
        "own_range_held": float(inside.mean()) if len(joined) else float("nan"),
        "mean_width_mw": float((joined["nd_p95_mw"] - joined["nd_p05_mw"]).mean())
        if len(joined)
        else 0.0,
        "mean_abs_error_mw": float((joined["nd_forecast_mw"] - joined["nd_actual_mw"]).abs().mean())
        if len(joined)
        else 0.0,
        "quantile_crossings_fixed": crossings,
    }


MAPPING = """\
# How Calibra reads this customer's files. Written by make_export.py.
agent_id = "openstef-xgboost"
timezone = "UTC"
period = "1D"
target_name = "nd_evening_peak"
unit = "MW"

[columns]
target_start = "delivery_day"
as_of = "created_at"
point = "nd_forecast_mw"
lower = "nd_p05_mw"
upper = "nd_p95_mw"
day_type = "day_class"
model_version = "model_version"

[features]
nd_last_week_same_time_mw = "nd_last_week_same_time_mw"
nd_latest_mw = "nd_latest_mw"

[outcomes]
realised = "nd_actual_mw"
"""


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--csv", nargs="+", type=Path, required=True, help="NESO historic demand CSV files"
    )
    p.add_argument("--out", type=Path, default=Path("export"))
    p.add_argument("--first-day", default="2024-07-01", help="first delivery day (UTC date)")
    p.add_argument("--last-day", default="2025-06-30", help="last delivery day (UTC date)")
    p.add_argument("--train-days", type=int, default=365, help="days of past data used to train")
    p.add_argument("--refit-days", type=int, default=14, help="retrain every N delivery days")
    p.add_argument(
        "--issue-hour", type=int, default=9, help="UTC hour the forecast is made, day before"
    )
    p.add_argument("--target-hour", type=int, default=17, help="UTC hour of the forecast target")
    p.add_argument("--load-col", default="ND")
    args = p.parse_args()
    warnings.filterwarnings("ignore")
    load = read_neso(args.csv, args.load_col)
    check_load(load)
    first, last = load.index.min(), load.index.max()
    print(f"read {len(load)} hourly values, {first:%Y-%m-%d} to {last:%Y-%m-%d}")
    summary = export(
        load, args.first_day, args.last_day, args.out, args.train_days, args.refit_days,
        args.issue_hour, args.target_hour,
    )  # fmt: skip
    print(f"wrote {args.out}/forecasts.csv, outcomes.csv, calibra.toml")
    for key, value in summary.items():
        print(f"  {key}: {value:.3f}" if isinstance(value, float) else f"  {key}: {value}")


if __name__ == "__main__":
    main()
