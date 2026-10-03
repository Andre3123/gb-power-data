"""Download half-hourly Market Index Data (MID) prices from Elexon."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from elexon_bmrs import BMRSClient

RAW_DIR = Path("data/raw")
CHUNK_DAYS = 7  # small requests; the API's own range limit is unverified

CAMEL_TO_SNAKE = {
    "startTime": "start_time",
    "settlementDate": "settlement_date",
    "settlementPeriod": "settlement_period",
    "dataProvider": "data_provider",
}


def _rows(response) -> list[dict]:
    """Accept the library's typed response or its raw-dict fallback."""
    if hasattr(response, "data"):
        return [r.model_dump() for r in (response.data or [])]
    return response.get("data", [])


def fetch_mid(start: date, end: date) -> pd.DataFrame:
    client = BMRSClient()
    frames = []
    day = start - timedelta(days=1)   # BST settlement days start 23:00 UTC the day before
    last = end + timedelta(days=1)
    while day <= last:
        stop = min(day + timedelta(days=CHUNK_DAYS), last + timedelta(days=1))
        resp = client.get_balancing_pricing_market_index(
            from_=f"{day}T00:00Z", to_=f"{stop}T00:00Z"
        )
        frames.append(pd.DataFrame(_rows(resp)))
        day = stop
    df = pd.concat(frames, ignore_index=True).rename(columns=CAMEL_TO_SNAKE)
    df = df.drop_duplicates(subset=["start_time", "data_provider"])
    df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
    df = df[(df["settlement_date"] >= start) & (df["settlement_date"] <= end)]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RAW_DIR / f"mid_{start}_{end}.parquet", index=False)
    return df


def main() -> None:
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])
    df = fetch_mid(start, end)
    summary = df.groupby("data_provider").agg(
        periods=("price", "size"),
        zero_volume=("volume", lambda v: int((v == 0).sum())),
        mean_price=("price", "mean"),
        min_price=("price", "min"),
        max_price=("price", "max"),
    )
    print(f"\nMID prices {start} to {end}\n")
    print(summary.round(2).to_string())


if __name__ == "__main__":
    main()