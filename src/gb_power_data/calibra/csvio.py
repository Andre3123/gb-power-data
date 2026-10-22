"""CSV front door: one row per forecast, one row per outcome.

Forecast columns (required in bold in docs/byof.md): forecast_id, agent_id, target_start,
target_end, as_of, point; optional model_version, data_cutoff, target_name, unit, lower,
upper, day_type, and any number of feature:<name>, asset:<name>, asset_source:<name> columns.
Outcome columns: forecast_id, agent_id, realised; optional truth_hash, realised_class.
Times are ISO 8601 with a UTC offset, e.g. 2025-01-08T00:00:00+00:00.
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import ValidationError

from gb_power_data.calibra.schema import Forecast, Outcome

FORECAST_COLUMNS = [
    "forecast_id", "agent_id", "model_version", "target_start", "target_end", "as_of",
    "data_cutoff", "target_name", "unit", "point", "lower", "upper", "day_type",
]  # fmt: skip
OUTCOME_COLUMNS = ["forecast_id", "agent_id", "realised", "truth_hash", "realised_class"]
PREFIXES = ("feature:", "asset:", "asset_source:")


class CsvError(Exception):
    """Rows that cannot be read; nothing is written when this is raised."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.problems = problems
        shown = "\n".join(f"  {p}" for p in problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"{path}: {len(problems)} problem(s), nothing was written\n{shown}{more}")


def _rows(path: Path, known: list[str], prefixes: tuple[str, ...]) -> list[tuple[int, dict]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        unknown = [h for h in header if h not in known and not h.startswith(prefixes)]
        if unknown:
            raise CsvError(path, [f"unknown column(s): {', '.join(unknown)}"])
        return [(i, row) for i, row in enumerate(reader, start=2)]  # line 1 is the header


def _value(text: str) -> float | str:
    try:
        return float(text)
    except ValueError:
        return text


def read_forecasts(path: str | Path) -> list[Forecast]:
    path = Path(path)
    out, problems, seen = [], [], set()
    for line, row in _rows(path, FORECAST_COLUMNS, PREFIXES):
        data: dict = {"features": {}, "asset": {}, "asset_source": {}}
        try:
            for key, raw in row.items():
                text = (raw or "").strip()
                if not text:
                    continue
                if key.startswith("feature:"):
                    data["features"][key.removeprefix("feature:")] = float(text)
                elif key.startswith("asset_source:"):
                    data["asset_source"][key.removeprefix("asset_source:")] = text
                elif key.startswith("asset:"):
                    data["asset"][key.removeprefix("asset:")] = _value(text)
                else:
                    data[key] = text
            f = Forecast(**data)
        except (ValidationError, ValueError) as e:
            problems.append(f"line {line}: {_short(e)}")
            continue
        if f.claim_id in seen:
            problems.append(f"line {line}: forecast_id {f.forecast_id} repeats for {f.agent_id}")
        seen.add(f.claim_id)
        out.append(f)
    if problems:
        raise CsvError(path, problems)
    return out


def read_outcomes(path: str | Path) -> list[Outcome]:
    path = Path(path)
    out, problems = [], []
    for line, row in _rows(path, OUTCOME_COLUMNS, ()):
        data = {k: v.strip() for k, v in row.items() if v and v.strip()}
        try:
            out.append(Outcome(**data))
        except ValidationError as e:
            problems.append(f"line {line}: {_short(e)}")
    if problems:
        raise CsvError(path, problems)
    return out


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(str(x) for x in err['loc']) or 'row'}: {err['msg']}" for err in e.errors()
        )
    return str(e)


def write_templates(folder: str | Path) -> tuple[Path, Path]:
    """Two example files a new user can copy: the format, filled with a realistic example."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fpath, opath = folder / "forecasts.csv", folder / "outcomes.csv"
    header = [
        *FORECAST_COLUMNS,
        "feature:price_spread_gbp_mwh",
        "feature:wind_forecast_gw",
        "asset:round_trip_efficiency",
        "asset_source:round_trip_efficiency",
    ]
    rows = [
        ["2025-01-08", "my-optimiser", "v1.2", "2025-01-08T00:00:00+00:00",
         "2025-01-09T00:00:00+00:00", "2025-01-07T00:00:00+00:00", "2025-01-06T23:30:00+00:00",
         "daily_revenue", "GBP", "15200", "12000", "18400", "normal", "64.5", "11.2", "0.87",
         "datasheet"],
        ["2025-01-09", "my-optimiser", "v1.2", "2025-01-09T00:00:00+00:00",
         "2025-01-10T00:00:00+00:00", "2025-01-08T00:00:00+00:00", "2025-01-07T23:30:00+00:00",
         "daily_revenue", "GBP", "21800", "", "", "wild", "118.0", "4.9", "0.87", "datasheet"],
    ]  # fmt: skip
    with fpath.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([header, *rows])
    with opath.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([OUTCOME_COLUMNS, ["2025-01-08", "my-optimiser", "13950", "", ""]])
    return fpath, opath
