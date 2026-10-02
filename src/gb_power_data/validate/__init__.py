"""Clean and validate APX Market Index prices."""

import sys
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

PROCESSED_DIR = Path("data/processed")
MAX_FILL_GAP = 2  # periods; longer gaps drop the whole day

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


def clean_apx(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    df = raw[raw["data_provider"] == "APXMIDP"].copy()
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["settlement_date"] = df["settlement_date"].astype(str)
    df = df.sort_values("start_time").reset_index(drop=True)

    df["imputed"] = df["volume"] == 0
    df.loc[df["imputed"], "price"] = float("nan")

    run_id = (df["imputed"] != df["imputed"].shift()).cumsum()
    gap_len = df.groupby(run_id)["imputed"].transform("sum")
    too_long = df["imputed"] & (gap_len > MAX_FILL_GAP)
    dropped = sorted(df.loc[too_long, "settlement_date"].unique())

    df["price"] = df["price"].interpolate(limit_area="inside")
    df = df[~df["settlement_date"].isin(dropped)]
    cols = ["start_time", "settlement_date", "settlement_period", "price", "imputed"]
    return df[cols].reset_index(drop=True), dropped


def main() -> None:
    raw = pd.read_parquet(sys.argv[1])
    clean, dropped = clean_apx(raw)
    validated = schema.validate(clean)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "prices_apx.parquet"
    validated.to_parquet(out, index=False)
    print(f"\nRows kept: {len(validated)}")
    print(f"Imputed periods kept: {int(validated['imputed'].sum())}")
    print(f"Days dropped: {dropped}")
    print(f"Saved: {out}\n")