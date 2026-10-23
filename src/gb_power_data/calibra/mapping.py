"""Plug in your own columns: a mapping file says which of your columns is which.

A customer's export keeps its own names (rev_forecast, wind_gw, created_at, ...). The mapping,
`calibra.toml`, tells Calibra how to read it:

    agent_id = "acme-optimiser"
    timezone = "Europe/London"   # for times written without an offset
    period = "1D"                # each forecast covers one day from target_start

    [columns]                    # Calibra field = your column
    target_start = "delivery_day"
    as_of = "created_at"
    point = "rev_forecast"

    [features]                   # name it in Calibra = your column
    wind_gw = "wind_forecast_gw"

    [assets]                     # a constant, or a column
    round_trip_efficiency = { value = 0.87, source = "Megapack datasheet" }

    [outcomes]
    realised = "actual_revenue"  # matched to forecasts on target_start unless forecast_id is

`calibra init your_file.csv` writes a first draft by guessing from your column names.
"""

from __future__ import annotations

import csv
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from gb_power_data.calibra.schema import Forecast, Outcome

FORECAST_FIELDS = (
    "forecast_id", "target_start", "target_end", "as_of", "data_cutoff", "point",
    "lower", "upper", "day_type", "model_version",
)  # fmt: skip
TIME_FIELDS = ("target_start", "target_end", "as_of", "data_cutoff")
NUMBER_FIELDS = ("point", "lower", "upper")
OUTCOME_FIELDS = ("forecast_id", "target_start", "realised", "truth_hash", "realised_class")


class MappingError(Exception):
    """The mapping or the data does not fit; nothing is written."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        shown = "\n".join(f"  {p}" for p in problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"{len(problems)} problem(s), nothing was written\n{shown}{more}")


@dataclass
class Mapping:
    agent_id: str
    columns: dict[str, str]
    features: dict[str, str] = field(default_factory=dict)
    assets: dict[str, dict] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    timezone: str = "UTC"
    period: str | None = None
    target_name: str = "value"
    unit: str = "unit"
    model_version: str = "unknown"

    @classmethod
    def load(cls, path: str | Path) -> Mapping:
        """From calibra.toml, or from the .json file the web app saves."""
        text = Path(path).read_text(encoding="utf-8")
        if Path(path).suffix.lower() == ".json":
            import json

            return cls.from_dict(json.loads(text))
        return cls.from_dict(tomllib.loads(text))

    @classmethod
    def from_dict(cls, d: dict) -> Mapping:
        problems = []
        known = {"agent_id", "columns", "features", "assets", "outcomes", "timezone", "period",
                 "target_name", "unit", "model_version"}  # fmt: skip
        problems += [f"unknown setting '{k}'" for k in d if k not in known]
        if "agent_id" not in d:
            problems.append("agent_id is missing")
        cols = d.get("columns", {})
        problems += [
            f"[columns] {k}: not a Calibra field" for k in cols if k not in FORECAST_FIELDS
        ]
        for need in ("target_start", "point"):
            if need not in cols:
                problems.append(f"[columns] {need} is required")
        if "target_end" not in cols and not d.get("period"):
            problems.append('give [columns] target_end or a period such as period = "1D"')
        if "as_of" not in cols:
            problems.append("[columns] as_of is required: when each forecast was made")
        outs = d.get("outcomes", {})
        problems += [
            f"[outcomes] {k}: not an outcome field" for k in outs if k not in OUTCOME_FIELDS
        ]
        for name, spec in d.get("assets", {}).items():
            if not isinstance(spec, dict) or ("value" in spec) == ("column" in spec):
                problems.append(f'[assets] {name}: give either value = ... or column = "..."')
        try:
            pd.Timestamp("2025-01-01").tz_localize(d.get("timezone", "UTC"))
        except Exception:
            problems.append(f"timezone '{d.get('timezone')}' is not a known time zone")
        if d.get("period"):
            try:
                pd.Timedelta(d["period"])
            except ValueError:
                problems.append(f"period '{d['period']}' is not a duration (e.g. 1D, 30min)")
        if problems:
            raise MappingError(problems)
        return cls(
            agent_id=d["agent_id"],
            columns=cols,
            features=d.get("features", {}),
            assets=d.get("assets", {}),
            outcomes=outs,
            timezone=d.get("timezone", "UTC"),
            period=d.get("period"),
            target_name=d.get("target_name", "value"),
            unit=d.get("unit", "unit"),
            model_version=d.get("model_version", "unknown"),
        )

    # ---- reading the customer's rows ----------------------------------------------------

    def _time(self, text: str) -> pd.Timestamp:
        ts = pd.Timestamp(text)
        if ts.tzinfo is None:
            ts = ts.tz_localize(self.timezone, ambiguous="raise", nonexistent="raise")
        return ts

    def forecasts(self, rows: list[dict[str, str]]) -> list[Forecast]:
        missing = self._missing_columns(rows, [*self.columns.values(), *self.features.values(),
                                               *self._asset_columns()])  # fmt: skip
        if missing:
            raise MappingError(missing)
        out, problems = [], []
        for line, row in enumerate(rows, start=2):
            try:
                out.append(self._forecast(row))
            except (ValidationError, ValueError, TypeError) as e:
                problems.append(f"line {line}: {_short(e)}")
        if problems:
            raise MappingError(problems)
        return out

    def _forecast(self, row: dict[str, str]) -> Forecast:
        get = {k: (row.get(col) or "").strip() for k, col in self.columns.items()}
        data: dict = {"agent_id": self.agent_id, "target_name": self.target_name,
                      "unit": self.unit, "model_version": self.model_version}  # fmt: skip
        for k, text in get.items():
            if not text:
                continue
            if k in TIME_FIELDS:
                data[k] = self._time(text).to_pydatetime()
            elif k in NUMBER_FIELDS:
                data[k] = _number(text, k)
            else:
                data[k] = text
        if "target_end" not in data and self.period and "target_start" in data:
            data["target_end"] = data["target_start"] + pd.Timedelta(self.period).to_pytimedelta()
        if "forecast_id" not in data and "target_start" in data:
            data["forecast_id"] = default_id(data["target_start"])
        data["features"] = {
            name: _number(row[col], name)
            for name, col in self.features.items()
            if (row.get(col) or "").strip()
        }
        data["asset"], data["asset_source"] = {}, {}
        for name, spec in self.assets.items():
            if "value" in spec:
                data["asset"][name] = spec["value"]
            elif (row.get(spec["column"]) or "").strip():
                data["asset"][name] = _number(row[spec["column"]], name) * spec.get("scale", 1.0)
            if spec.get("source") and name in data["asset"]:
                data["asset_source"][name] = spec["source"]
        return Forecast(**data)

    def outcomes_from(self, rows: list[dict[str, str]]) -> list[Outcome]:
        o = self.outcomes
        if "realised" not in o:
            raise MappingError(["[outcomes] realised is required: your column of actual values"])
        key = o.get("forecast_id")
        start = o.get("target_start", self.columns.get("target_start"))
        missing = self._missing_columns(rows, [c for c in (o["realised"], key or start) if c])
        if missing:
            raise MappingError(missing)
        out, problems = [], []
        for line, row in enumerate(rows, start=2):
            try:
                fid = row[key].strip() if key else default_id(self._time(row[start].strip()))
                data = {"forecast_id": fid, "agent_id": self.agent_id,
                        "realised": _number(row[o["realised"]], "realised")}  # fmt: skip
                for f in ("truth_hash", "realised_class"):
                    if f in o and (row.get(o[f]) or "").strip():
                        data[f] = row[o[f]].strip()
                out.append(Outcome(**data))
            except (ValidationError, ValueError, TypeError, KeyError) as e:
                problems.append(f"line {line}: {_short(e)}")
        if problems:
            raise MappingError(problems)
        return out

    def _asset_columns(self) -> list[str]:
        return [s["column"] for s in self.assets.values() if "column" in s]

    @staticmethod
    def _missing_columns(rows: list[dict[str, str]], needed: list[str]) -> list[str]:
        have = set(rows[0]) if rows else set()
        return [f"column '{c}' is not in the file" for c in dict.fromkeys(needed) if c not in have]


def default_id(start) -> str:
    """Forecast id from the target start when the customer has none: 2025-01-08T00-00Z."""
    ts = pd.Timestamp(start).tz_convert("UTC")
    return ts.strftime("%Y-%m-%dT%H-%MZ")


def _number(text: str, name: str) -> float:
    try:
        return float(str(text).strip().replace(",", ""))
    except ValueError:
        raise ValueError(f"{name}: '{text}' is not a number") from None


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in r['loc']) or 'row'}: {r['msg']}"
                         for r in e.errors())  # fmt: skip
    return str(e)


def read_table(path: str | Path) -> list[dict[str, str]]:
    """Rows of a CSV or Parquet file, every value as text."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
        return [
            {k: "" if pd.isna(v) else str(v) for k, v in r.items()} for r in df.to_dict("records")
        ]
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ---- drafting a mapping from a customer's file -------------------------------------------

GUESSES = {
    # order matters: the first column to match a field is taken, so the specific fields go first
    "as_of": r"creat|issue|as_of|asof|made|run_time|forecast_time|generated|publish",
    "target_start": r"deliver|target|settlement|period_start|^date$|^day$|start|(^|_)(time|timestamp|datetime|ds)$",
    "target_end": r"(^|_)end(_|$)",
    "lower": r"(^|_)(p|q)0?[15]0?(_|$)|lower|(^|_)lo(w)?(_|$)|lcb|lb$",
    "upper": r"(^|_)(p|q)(9[05])(_|$)|upper|(^|_)hi(gh)?(_|$)|ucb|ub$",
    "point": r"rev|forecast|predict|point|expect|^plan|yhat|median|(^|_)mean(_|$)",
    "day_type": r"regime|class|day_type|category",
    "forecast_id": r"^id$|forecast_id|run_id",
}
OUTCOME_GUESS = r"actual|realis|realiz|outturn|settled|observed"
ASSET_GUESS = r"soc|state_of_charge|efficien|capacity|soh|degrad|wear|cycle"  # asset numbers


def guess(rows: list[dict[str, str]]) -> dict:
    """Which column is which, guessed from the names: Calibra fields, then numeric inputs."""
    header = list(rows[0]) if rows else []
    taken: set[str] = set()
    picks: dict[str, str] = {}
    for fieldname, pattern in GUESSES.items():
        for col in header:
            if col not in taken and re.search(pattern, col, re.IGNORECASE):
                picks[fieldname] = col
                taken.add(col)
                break
    actual = next((c for c in header if re.search(OUTCOME_GUESS, c, re.IGNORECASE)), None)
    if actual:
        taken.add(actual)
    numeric = [c for c in header if c not in taken and _looks_numeric(rows, c)]
    assets = [c for c in numeric if re.search(ASSET_GUESS, c, re.IGNORECASE)]
    return {
        "columns": picks,
        "features": {_key(c): c for c in numeric if c not in assets},
        "assets": {_key(c): c for c in assets},
        "realised": actual,
        "header": header,
        "samples": {c: [r.get(c, "") for r in rows[:3]] for c in header},
    }


def draft(forecast_rows: list[dict[str, str]], agent_id: str = "my-optimiser") -> str:
    """A first calibra.toml from a file's columns; every guess is marked for the user to check."""
    g = guess(forecast_rows)
    picks, actual = g["columns"], g["realised"]
    numeric = list(g["features"].values())
    lines = [
        "# calibra.toml: how Calibra reads your files. Check every line marked CHECK.",
        f'agent_id = "{agent_id}"',
        'timezone = "UTC"        # CHECK: the zone of times written without an offset',
    ]
    if "target_end" not in picks:
        lines.append('period = "1D"           # CHECK: how long each forecast covers')
    lines += ['target_name = "value"', 'unit = "unit"', "", "[columns]"]
    for fieldname in GUESSES:
        if fieldname in picks:
            lines.append(f'{fieldname} = "{picks[fieldname]}"   # CHECK (guessed)')
        elif fieldname in ("target_start", "as_of", "point"):
            lines.append(f'# {fieldname} = "..."   # REQUIRED: not found, fill in')
    lines += ["", "[features]   # inputs Calibra checks for scope; remove any you do not want"]
    lines += [f'{_key(c)} = "{c}"' for c in numeric]
    lines += [
        "",
        "[assets]   # physical assumptions: a column, or a constant such as",
        '# round_trip_efficiency = { value = 0.87, source = "datasheet" }',
        *[f'{k} = {{ column = "{c}" }}   # CHECK (guessed)' for k, c in g["assets"].items()],
        "",
        "[outcomes]",
        f'realised = "{actual}"   # CHECK (guessed)'
        if actual
        else '# realised = "..."   # REQUIRED',
    ]
    return "\n".join(lines) + "\n"


def _looks_numeric(rows: list[dict[str, str]], col: str) -> bool:
    sample = [r.get(col, "") for r in rows[:20] if (r.get(col) or "").strip()]
    try:
        return bool(sample) and all(float(v.replace(",", "")) is not None for v in sample)
    except ValueError:
        return False


def _key(col: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", col).strip("_").lower() or "feature"
