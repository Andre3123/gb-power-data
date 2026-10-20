"""Check the ledger's hash chain: was any claim or resolution changed after it was written?

Run: uv run gbpd-verify                  (prints the head to publish)
     uv run gbpd-verify --head <hash>    (also checks against a head published earlier)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gb_power_data.ledger.store import Ledger

LEDGER = Path("data/ledger/ledger.duckdb")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gbpd-verify", description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=LEDGER, help=f"default: {LEDGER}")
    parser.add_argument("--head", help="a head hash published earlier")
    args = parser.parse_args(argv)
    if not args.ledger.is_file():
        print(f"gbpd-verify: error: ledger file not found: {args.ledger}", file=sys.stderr)
        return 1
    ledger = Ledger(args.ledger)  # chains an older ledger once, on first open
    try:
        report = ledger.verify(expected_head=args.head)
    finally:
        ledger.close()
    print(f"chain entries: {report.entries} ({report.backfilled} backfilled from before the chain)")
    print(f"head: {report.head}")
    if report.ok:
        print("OK: every row matches the hash it was written with")
        return 0
    print(f"BROKEN: {len(report.problems)} problem(s)")
    for p in report.problems[:20]:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())