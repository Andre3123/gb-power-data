# Calibra for your own optimiser: bring your own forecasts

Calibra does not replace your optimiser or your agent. You keep forecasting the way you do
today. Calibra:

1. **records each forecast before its target period** in a hash-chained ledger, so nobody can
   change it after the outcome is known;
2. **gives it an honest range** (90% by default), learned from *your* past errors, per day type
   when there is enough history;
3. **checks the inputs** you used against the inputs your past forecasts were made from, and
   flags the ones it has not seen (scope);
4. **scores it** once you send the outcome: did the range hold, by how much was the lower bound
   missed, and how did your own range do on the same forecasts.

There are four ways in, all using the same engine:

| You are | Use |
|---|---|
| an analyst or optimiser team with exports | the `calibra` command and your own CSV files |
| your optimiser's software | the HTTPS API with your key |
| an engineer | Python: `from gb_power_data.calibra import Calibra, Forecast, Outcome` |
| an AI agent | the `calibra-mcp` server (Model Context Protocol) |

## Start in five minutes

```bash
uv run calibra template my_first_run          # writes forecasts.csv and outcomes.csv
uv run calibra replay my_first_run/forecasts.csv my_first_run/outcomes.csv
uv run calibra report my-optimiser
uv run calibra verify
```

`replay` runs a past track record through Calibra in time order, exactly as if live: before
each forecast, only the outcomes whose target period had already ended are used. This is how
you onboard: send your history, and from then on send forecasts as you make them.

The ledger is `data/calibra/ledger.duckdb` unless you set `CALIBRA_LEDGER` or pass `--ledger`.

## Your own files, as they are

You do not need to rename your columns. A mapping file, `calibra.toml`, says which of your
columns is which. Let Calibra draft it from your file, check the lines marked `CHECK`, then run:

```bash
uv run calibra init exports/forecasts.csv --agent acme-optimiser     # writes calibra.toml
uv run calibra replay --map calibra.toml exports/forecasts.csv exports/actuals.csv
```

A complete mapping:

```toml
agent_id = "acme-optimiser"
timezone = "Europe/London"   # for times written without an offset (clock changes handled)
period = "1D"                # each forecast covers one day from target_start
target_name = "daily_revenue"
unit = "GBP"

[columns]                    # Calibra field = your column
target_start = "delivery_day"
as_of = "created_at"
point = "rev_forecast"
lower = "p10"                # your own range, optional
upper = "p90"
day_type = "regime"          # optional

[features]                   # inputs to check for scope: name = your column
wind_gw = "wind_forecast_gw"
spread = "da_spread"

[assets]                     # physical assumptions: a constant, or a column
round_trip_efficiency = { value = 0.87, source = "Megapack datasheet" }
soc_start = { column = "soc_start_pct", scale = 0.01, source = "BMS" }

[outcomes]
realised = "actual_revenue"  # matched to forecasts on target_start, or set forecast_id = "..."
```

Without a `forecast_id` column, each forecast's id is its target start in UTC
(`2025-03-01T00-00Z`), and the outcome file is matched on the same column.

## The web app (hosted Calibra)

Open your Calibra address in a browser (for example `https://<your-calibra-host>/`) and sign in
with your key. Then:

1. **Add history**: upload your forecasts file and your actuals file, as exported.
2. **Your columns**: each column with its first values and a dropdown; Calibra pre-selects its
   guess. Choose your time zone and how long each forecast covers.
3. **Check**: the first rows read the way they will be recorded. Nothing is written yet.
4. **Import**: your history is replayed in time order, then your track record opens.

**Send new forecasts** shows ready-to-copy `curl`, Python and MCP snippets for your agent.

## The HTTPS API (hosted Calibra)

Your software calls Calibra with its API key: `Authorization: Bearer cal_...`. Each customer
has a separate ledger. Full interactive documentation is at `/docs` on the server.

| Call | What it does |
|---|---|
| `POST /v1/forecasts` | record forecasts (JSON, same fields as the file); retrying is safe |
| `POST /v1/outcomes` | score them once the period has ended |
| `POST /v1/scope-check` | check inputs without recording anything |
| `GET /v1/agents/{agent_id}/report` | the track record |
| `GET /v1/agents/{agent_id}/status` | how the next range would be learned, and the scope bands |
| `GET /v1/ledger/verify` | check the hash chain |
| `POST /v1/mapping/draft`, `/v1/mapping/preview`, `/v1/import` | onboard your files |

```bash
curl -X POST https://<your-calibra-host>/v1/forecasts \
  -H "Authorization: Bearer $CALIBRA_KEY" -H "Content-Type: application/json" \
  -d '{"forecasts": [{"forecast_id": "2026-10-22", "agent_id": "acme-optimiser",
        "target_start": "2026-10-22T00:00:00+01:00", "target_end": "2026-10-23T00:00:00+01:00",
        "as_of": "2026-10-21T09:30:00+01:00", "point": 18250, "day_type": "calm",
        "features": {"wind_gw": 8.1, "spread": 62.0}}]}'
```

Running the server (operator): `uv run calibra customer create acme` prints the customer's key
once (only its hash is stored), then `uv run calibra serve --host 0.0.0.0`.

## The forecast file

One row per forecast. Times are ISO 8601 **with a UTC offset**, e.g. `2025-01-08T00:00:00+00:00`.

| Column | Required | Meaning |
|---|---|---|
| `forecast_id` | yes | your id for this forecast; letters, digits, `.` `_` `-` |
| `agent_id` | yes | which optimiser or agent made it; its track record is kept separately |
| `target_start`, `target_end` | yes | the period forecast, e.g. one settlement day |
| `as_of` | yes | when the forecast was made; must be before `target_start` |
| `point` | yes | your forecast, e.g. tomorrow's revenue |
| `data_cutoff` | no | the latest data the forecast used; defaults to `as_of`, may not be later |
| `model_version` | no | your model or prompt version |
| `target_name`, `unit` | no | e.g. `daily_revenue`, `GBP` |
| `lower`, `upper` | no | your own range, if you have one; scored next to Calibra's |
| `day_type` | no | your own class (e.g. `calm` / `wild`), known when the forecast is made |
| `feature:<name>` | no, any number | the inputs to check for scope, e.g. `feature:wind_forecast_gw` |
| `asset:<name>` | no, any number | physical assumptions used, e.g. `asset:round_trip_efficiency` |
| `asset_source:<name>` | no | where that assumption comes from, e.g. `datasheet`, `measured 2026-09` |

## The outcome file

| Column | Required | Meaning |
|---|---|---|
| `forecast_id`, `agent_id` | yes | which forecast this settles |
| `realised` | yes | what really happened, in the same unit |
| `truth_hash` | no | a fingerprint of your source data (meter file, settlement report) |
| `realised_class` | no | a label that depends on the outcome |

A file with any bad row is refused as a whole: the error lists every bad line, and nothing is
written. Sending the same outcome twice changes nothing; sending a corrected one adds version 2
and keeps version 1.

## How the range is learned

For a forecast made at `as_of`, Calibra takes the same agent's forecasts whose target period
ended by `as_of` and whose outcome it holds, and measures how far each outcome landed from its
point. Split conformal prediction then picks the distance that covers 90% of them, with the
finite-sample correction (Angelopoulos & Bates, 2021).

* **mondrian**: the forecast's `day_type` has at least 20 past outcomes, so it gets a width
  learned from that type alone. Wild days get wide ranges, calm days narrow ones.
* **pooled**: not enough history for that type yet, so the width is learned from all types.
* **none**: fewer than 9 past outcomes, too few for a 90% range. The forecast is still
  recorded and scored, with no range.

## Scope

For each `feature:` column, Calibra learns the 1st to 99th percentile of your past values.
A new forecast is flagged when a feature is outside that band, missing, or has too little
history. A flag is a warning, not a block: the forecast is still recorded, with the flags and,
if you give one, your reason for going ahead. The report then shows how much of the missed
money fell on flagged forecasts.

## For AI agents (MCP)

Add the server to any MCP client, for example:

```json
{
  "mcpServers": {
    "calibra": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gb-power-data", "calibra-mcp"],
      "env": { "CALIBRA_LEDGER": "/path/to/your/ledger.duckdb" }
    }
  }
}
```

With a hosted Calibra, the server runs next to your agent and keeps the records on the host:

```json
{
  "mcpServers": {
    "calibra": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Andre3123/gb-power-data", "calibra-mcp"],
      "env": { "CALIBRA_API_URL": "https://<your-calibra-host>", "CALIBRA_KEY": "cal_..." }
    }
  }
}
```

Tools: `check_scope`, `submit_forecast`, `calibration_status`, `agent_report`.
There is no tool to record outcomes: they come from your meter or settlement data through
`calibra resolve`, so an agent never grades itself.

## What Calibra guarantees, and what it does not

* A recorded forecast cannot be changed or deleted without `calibra verify` reporting it.
  Publish the head it prints (in git, or to your lender) to anchor the record.
* A range never uses an outcome that was not known when the forecast was made.
* The 90% holds on average over many forecasts if the future errors behave like the past ones
  (exchangeability). When inputs move outside the learned bands, that assumption is in doubt:
  that is what the scope flags tell you.
* Calibra does not check that your outcomes are true. Give a `truth_hash` so they can be traced.
