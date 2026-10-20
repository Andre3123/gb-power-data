#!/usr/bin/env bash
# Reproduce every Calibra result from scratch: download prices, build the ledger, score it,
# write the report and verify the hash chain. Works in bash and in Git Bash on Windows.
#
# Run from anywhere:  bash scripts/reproduce.sh
# Needs: uv, and internet access to the Elexon BMRS API (no key needed).
#
# Rough run time: about 17 minutes from scratch (measured on a Linux VM, 2026-09-30 data).
# The first gbpd-value run is slow: it solves the perfect-foresight plan of every day
# (about a second per day, so ~10 minutes for 622 days) and caches it in data/processed/,
# so later runs are fast.

set -euo pipefail
cd "$(dirname "$0")/.."

PRICES_START=2022-12-30  # two days before learning starts: claims plan from D-2 prices
PRICES_END=2026-09-30
LEARN_START=2023-01-01
LEARN_END=2024-12-31
TEST_START=2025-01-01
TEST_END=2026-09-30

step() {
    echo
    echo "=== $* ==="
    uv run "$@"
}

step gbpd-prices "$PRICES_START" "$PRICES_END"
step gbpd-validate "data/raw/mid_${PRICES_START}_${PRICES_END}.parquet" "$PRICES_START" "$PRICES_END"
step gbpd-backtest "$TEST_START" "$TEST_END"
step gbpd-calibrate "$LEARN_START" "$LEARN_END" "$TEST_START" "$TEST_END"
step gbpd-physics "$TEST_START" "$TEST_END" 30
step gbpd-value "$TEST_START" "$TEST_END"
step gbpd-crosscheck 2025-01-01 2025-01-31
step gbpd-report "$TEST_START" "$TEST_END"
step gbpd-verify

echo
echo "=== done: report in reports/calibra_report.html ==="
