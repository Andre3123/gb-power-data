# Calibra

Calibra is a verification layer for AI agents that act on physical assets.

An agent makes a claim, for example "tomorrow this battery will earn between £X and £Y, with 90%
confidence". Calibra then:

- records the claim in a tamper-evident ledger (each row is hash-chained to the one before);
- scores the claim on what really happened;
- warns when a day is out of scope, unlike the history the ranges were learned from (ScopeGate);
- records the battery assumptions each claim rests on (power, energy, efficiency, wear cost,
  cycle cap).

The first asset is the Pillswood battery in Great Britain (98 MW / 196 MWh). Claims are about its
day-ahead trading revenue on APX prices.

**Live app:** https://calibra-mentha.fly.dev (web app at `/app`, API documentation at `/docs`,
health check at `/health`). It is one small server in London with one disk.

## Why this matters for datacentres and grid flexibility

Shell New Energy Challenge 2026, Theme 5: datacentres and grid-ready flexibility.

Hypothesis (not yet tested on datacentre data): a datacentre that offers flexibility to the grid
makes a promise about a physical quantity ("I can drop 20 MW for 2 hours tomorrow"). Whoever buys
that promise needs an honest range and a record nobody can change. Calibra does this for any
forecaster that can export its forecasts and its outcomes. **No datacentre data has been used so
far.** The tests below use a GB battery and GB national demand.

## Bring your own forecasts

Calibra does not replace your forecaster. You keep it. You send two CSV files (forecasts and
outcomes) and a short mapping file `calibra.toml` that says which column is which. Calibra replays
your history in time order, as if live, and then gives you an honest range, a scope check and a
track record. See `docs/byof.md`.

```bash
uv run calibra template my_first_run
uv run calibra replay my_first_run/forecasts.csv my_first_run/outcomes.csv
uv run calibra report my-optimiser
uv run calibra verify
```

### Test on a forecaster we did not build (stand-in customer)

OpenSTEF (Alliander, LF Energy) is a real open-source load forecaster. It plays "a customer's own
forecaster". It runs in its own environment (`examples/openstef_standin/`). Only two CSV files
cross over to Calibra.

- Target: GB National Demand (NESO historic demand data) at 17:00 UTC, one forecast per day, made
  at 09:00 UTC the day before. 365 delivery days, 1 July 2024 to 30 June 2025. OpenSTEF is
  retrained every 14 days on past data only.
- Calibra scored 355 of them (the first 10 had no history yet).

| | OpenSTEF's own 90% range | Calibra's 90% range |
|---|---|---|
| Share of days the real value was inside | **45.9%** | **87.6%** |
| Mean width | 2,755 MW | 6,290 MW |

- Calibra's lower bound was broken on 5.1% of days (18 of 355).
- 87.6% is 2.4 points under the 90% promise. With 355 days the sampling noise is about
  ±1.6 points, so it is consistent with 90%, but it does not prove 90%.
- The price is width: Calibra's range is 2.3 times wider, because the model is weak.
- **ScopeGate did not help here.** It flagged 78 of 355 days, and those days held only 10% of the
  missing shortfall (Pillswood: 78%). We sent only two weak inputs, and ScopeGate can only check
  inputs the customer sends.

To rerun it: download the NESO historic demand CSV files for 2023 to 2025 and follow
`examples/openstef_standin/README.md`.

## Results

Test period 2025-01-01 to 2026-09-30, 622 days. Three agents share the same dispatch plan and
differ only in their 90% range.

| | naive ±40% | split conformal | Mondrian |
|---|---|---|---|
| Coverage of the 90% range | 31.5% | 91.2% | 92.3% |
| Mean width | £9,413 | £33,490 | £28,433 |

- **Promise broken** (realised revenue below the lower bound): naive on 60% of days
  (£1.91m missing), Mondrian on 6% (£0.39m missing).
- **ScopeGate** flagged 63 days (10%), using only data from two days before. Those days hold 78% of
  Mondrian's missing money (56% without the single worst day, 10 Jan 2025).
- **Capture rate** of the persistence plan, net of wear: 26.6% of perfect-foresight revenue
  (£0.83m vs £3.11m a year).
- **Cross-check:** our optimiser matches PyPSA to £0.01 on all 31 days of January 2025.

To reproduce all of it, run `uv sync` and then `bash scripts/reproduce.sh` (bash or Git Bash on
Windows). It runs every step in order, prints each step's name and stops at the first error.
Data goes to `data/` (gitignored) and the report overwrites `reports/calibra_report.html`.

## Honest limits

- Day-ahead arbitrage only. No balancing mechanism, ancillary services or capacity market.
- Efficiency, wear cost and cycle cap are placeholders, not verified site values.
- The floor promise (the owner promises the lower bound as safe revenue) is a stylised use case.
- Day-ahead only. One asset (Pillswood) and one stand-in forecaster (OpenSTEF on national demand).
  OpenSTEF has no weather input, so it is a weak model; the stand-in is not a real client.
- The 63 flagged days on Pillswood are a small sample. On the OpenSTEF test ScopeGate did not
  find the failures (see above).
- Outcomes are trusted as sent. Calibra does not check them against a second source.
- Calibra shows that a forecaster is weak, not why. Error breakdown by condition, review queue,
  decision record and chance-constrained dispatch are roadmap items and are **not built**.
- One server in one region, running as root in its container. No backups beyond Fly.io's daily
  volume snapshots.
- Ledger rows written before 20 Oct 2026 were hash-chained retroactively (backfilled). The chain
  proves they have not changed since then, not since they were first written.

## Architecture

Everything lives in `src/gb_power_data/`:

| Folder | What it does |
|---|---|
| `agent/` | DSPy agent that makes claims through Calibra's MCP tools. |
| `calibration/` | Split conformal and Mondrian range widths learned from past mistakes. |
| `calibra/` | Bring-your-own-forecasts engine, CSV mapping, API with per-customer keys, web app (`web/`), MCP server. |
| `claims/` | Day-ahead revenue claims for Pillswood, recorded and resolved in the ledger. |
| `crosscheck/` | Checks our dispatch revenue against PyPSA, day by day. |
| `ingest/` | Downloads prices and BM unit data from Elexon BMRS. |
| `ledger/` | Append-only, hash-chained DuckDB ledger of claims and resolutions, and its scoring. |
| `mcp_server/` | MCP server exposing Calibra's tools to any agent or MCP client. |
| `optimise/` | Battery arbitrage dispatch in linopy, solved by HiGHS. |
| `physics/` | Battery assumptions behind each claim and how much they move it. |
| `pipelines/` | ZenML pipeline: download, then clean and validate prices. |
| `report/` | Self-contained HTML report on how the agents kept their promises. |
| `scopegate/` | ScopeGate: flags days unlike the learning history. |
| `validate/` | Cleans and validates APX prices. |
| `value/` | Turns claims into pounds: floor promise, ScopeGate in GBP, capture rate. |

## Commands

Run each with `uv run <command>` from the repo root.

| Command | What it does |
|---|---|
| `gbpd-find-bmu [KEYWORD ...]` | Search Elexon's BM unit list (default: pillswood, harmony, creyke). |
| `gbpd-prices START END` | Download half-hourly MID prices to `data/raw/`. |
| `gbpd-validate RAW_FILE FIRST LAST` | Clean and validate APX prices to `data/processed/prices_apx.parquet`. |
| `gbpd-pipeline START END` | Download and validate prices as one ZenML pipeline. |
| `gbpd-dispatch DAY` | Solve the optimal dispatch of one day on its real prices. |
| `gbpd-backtest START END` | Make, record and resolve naive ±40% claims over a date range. |
| `gbpd-calibrate LEARN_START LEARN_END TEST_START TEST_END` | Learn range widths, then claim and score the test period (naive, split conformal, Mondrian, Mondrian + ScopeGate). |
| `gbpd-mcp` | Start Calibra's MCP server (stdin/stdout; an MCP client starts it for you). |
| `gbpd-agent DAY [DAY ...]` | Let the AI agent claim the given days through the MCP server. |
| `gbpd-physics START END [STEP]` | How much of a claim's range comes from battery physics (every STEP-th day, default 7). |
| `gbpd-value START END` | Floor promise, ScopeGate in GBP and capture rate. |
| `gbpd-crosscheck START END` | Compare our dispatch revenue with PyPSA's, day by day. |
| `gbpd-report START END` | Write the HTML report to `reports/calibra_report.html`. |
| `gbpd-verify [--head HASH]` | Check the ledger's hash chain, optionally against a published head. |
| `gbpd-tune START END` | Tune the DSPy agent's prompt (needs `CALIBRA_LM` and an API key). |
| `calibra ...` | Bring your own forecasts: `template`, `init`, `replay`, `submit`, `resolve`, `report`, `status`, `verify`, `serve`, `customer`. |
| `calibra-mcp` | Calibra's MCP server for your own forecaster (no outcome tool, so an agent cannot score itself). |

## Agent setup

`gbpd-agent` needs two things in the environment:

- `CALIBRA_LM`: a LiteLLM model string, e.g. `anthropic/<model>` or `openai/<model>`.
- The API key of that provider, e.g. `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

Optional tracing with Opik: set `OPIK_API_KEY` and add opik for the run (it is not a project
dependency):

```bash
OPIK_API_KEY=... uv run --with opik gbpd-agent 2025-03-12
```

Never commit API keys. Keep them in your shell environment or a local file that is not tracked.

## Hosting

The app runs on Fly.io from the `Dockerfile` and `fly.toml` in this repo (London, 1 GB disk
mounted at `/data`, health check on `/health`).

```bash
fly deploy
uv run calibra customer create <name>   # prints the customer's key once; only its hash is stored
```

Run it locally with `uv run calibra serve`.

## Tests

`uv run pytest` (128 tests) and `uv run ruff check .` run on every pull request.
