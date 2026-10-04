"""ZenML pipeline: ingest Elexon prices, then clean and validate them.

The real work lives in ingest/ and validate/. This file only wires those
functions together as ZenML steps, so ZenML can be swapped out later.
"""

import sys
from datetime import date

import pandas as pd
from zenml import log_metadata, pipeline, step

from gb_power_data.ingest.prices import fetch_mid
from gb_power_data.validate.prices import clean_apx, schema


@step
def ingest_prices(start: str, end: str) -> pd.DataFrame:
    raw = fetch_mid(date.fromisoformat(start), date.fromisoformat(end))
    log_metadata(
        metadata={"rows": len(raw), "start": start, "end": end},
        infer_artifact=True,
    )
    return raw


@step
def validate_prices(raw: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    clean, dropped, absent = clean_apx(raw, start, end)
    validated = schema.validate(clean)
    log_metadata(
        metadata={
            "rows_kept": len(validated),
            "periods_missing_from_source": absent,
            "imputed_periods_kept": int(validated["imputed"].sum()),
            "days_dropped": dropped,
        },
        infer_artifact=True,
    )
    return validated


@pipeline
def price_pipeline(start: str, end: str) -> None:
    raw = ingest_prices(start, end)
    validate_prices(raw, start, end)


def main() -> None:
    price_pipeline(start=sys.argv[1], end=sys.argv[2])