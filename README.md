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
- Ledger rows written before 20 Oct 2026 were hash-chained retroactively (backfilled). The chain
  proves they have not changed since then, not since they were first written.

## Architecture

Everything lives in `src/gb_power_data/`:

| Folder | What it does |
|---|---|
| `agent/` | DSPy agent that makes claims through Calibra's MCP tools. |
| `calibration/` | Split conformal and Mondrian range widths learned from past mistakes. |
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
