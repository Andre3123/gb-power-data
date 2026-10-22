# OpenSTEF stand-in customer for Calibra

Hypothesis: a forecaster Calibra has never seen can be onboarded from two CSV files, and its own
90% range will be too narrow (it will hold clearly less than 90% of the time).

Stand-in, not a real client. Load only, no weather, national demand, untuned model.

## Run (own folder, own Python 3.12; `uv` installs it for you)

    uv run make_export.py --csv demanddata_2023.csv demanddata_2024.csv demanddata_2025.csv --out export

Needs NESO historic demand CSV files (columns SETTLEMENT_DATE, SETTLEMENT_PERIOD, ND).
Options: --first-day / --last-day (default 2024-07-01 to 2025-06-30), --refit-days 14.

## Then, in the main calibra project

    uv run calibra --ledger data/standin.duckdb replay --map examples/openstef_standin/export/calibra.toml examples/openstef_standin/export/forecasts.csv examples/openstef_standin/export/outcomes.csv
    uv run calibra --ledger data/standin.duckdb report openstef-xgboost
