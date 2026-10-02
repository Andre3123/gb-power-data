"""Clean and validate APX Market Index prices."""

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

PROCESSED_DIR = Path("data/processed")
MAX_FILL_GAP = 2  # periods; longer gaps drop the whole day
UK = "Europe/London"

schema = pa.DataFrameSchema(
    {
        "start_time": pa.Column(unique=True, nullable=False),
        "settlement_date": pa.Column(str),
        "settlement_period": pa.Column(int, pa.Check.in_range(1, 50)),
        "price": pa.Column(float, nullable=False),
        "imputed": pa.Column(bool),
    },
    checks=pa.Check(
        lambda d: d.groupby("settlement_date")["settlement_period"]
        .count()
        .isin([46, 48, 50])
        .all(),
        error="every settlement day must have 46, 48 or 50 periods",
    ),
    coerce=True,
)


def expected_grid(first: str, last: str) -> pd.DataFrame:
    """Every settlement period that should exist, from the UK calendar."""
    rows = []
    day = pd.Timestamp(first).date()
    end = pd.Timestamp(last).date()
    while day <= end:
        t0 = pd.Timestamp(day, tz=UK).tz_convert("UTC")
        t1 = pd.Timestamp(day + timedelta(days=1), tz=UK).tz_convert("UTC")
        n = int((t1 - t0) / pd.Timedelta(minutes=30))
        for p in range(n):
            rows.append((str(day), p + 1, t0 + pd.Timedelta(minutes=30 * p)))
        day += timedelta(days=1)
    return pd.DataFrame(rows, columns=["settlement_date", "settlement_period", "start_time"])


def clean_apx(raw: pd.DataFrame, first: str, last: str) -> tuple[pd.DataFrame, list[str], int]:
    df = raw[raw["data_provider"] == "APXMIDP"].copy()
    df["settlement_date"] = df["settlement_date"].astype(str)
    df["settlement_period"] = df["settlement_period"].astype(int)
    df = df[(df["settlement_date"] >= first) & (df["settlement_date"] <= last)]

    grid = expected_grid(first, last)
    keys = ["settlement_date", "settlement_period"]
    df = grid.merge(df[keys + ["price", "volume"]], on=keys, how="left")
    absent = int(df["volume"].isna().sum())

    df["imputed"] = df["volume"].isna() | (df["volume"] == 0)
    df.loc[df["imputed"], "price"] = float("nan")

    run_id = (df["imputed"] != df["imputed"].shift()).cumsum()
    gap_len = df.groupby(run_id)["imputed"].transform("sum")
    too_long = df["imputed"] & (gap_len > MAX_FILL_GAP)
    dropped = sorted(df.loc[too_long, "settlement_date"].unique())

    df["price"] = df["price"].interpolate(limit_area="inside")
    df = df[~df["settlement_date"].isin(dropped)]
    cols = ["start_time", "settlement_date", "settlement_period", "price", "imputed"]
    return df[cols].reset_index(drop=True), dropped, absent


def main() -> None:
    path, first, last = sys.argv[1], sys.argv[2], sys.argv[3]
    raw = pd.read_parquet(path)
    clean, dropped, absent = clean_apx(raw, first, last)
    validated = schema.validate(clean)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "prices_apx.parquet"
    validated.to_parquet(out, index=False)
    print(f"\nRows kept: {len(validated)}")
    print(f"Periods missing from the source: {absent}")
    print(f"Imputed periods kept: {int(validated['imputed'].sum())}")
    print(f"Days dropped: {dropped}")
    print(f"Saved: {out}\n")