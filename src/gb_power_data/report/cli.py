"""Write the Calibra HTML report for claims with target dates START..END (inclusive).

Run: uv run gbpd-report 2025-01-01 2026-09-30
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from gb_power_data.report.data import ReportError, load
from gb_power_data.report.render import write_report

LEDGER = Path("data/ledger/ledger.duckdb")
OUT = Path("reports/calibra_report.html")


def _iso_date(text: str) -> date:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date '{text}', expected YYYY-MM-DD") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gbpd-report",
        description="Write a self-contained HTML report on how the agents kept their promises.",
    )
    parser.add_argument("start", metavar="START", type=_iso_date, help="first target date")
    parser.add_argument("end", metavar="END", type=_iso_date, help="last target date")
    parser.add_argument("--ledger", type=Path, default=LEDGER, help=f"default: {LEDGER}")
    parser.add_argument("--out", type=Path, default=OUT, help=f"default: {OUT}")
    args = parser.parse_args(argv)
    if args.start > args.end:
        parser.error(f"START ({args.start}) is after END ({args.end})")
    try:
        ledger = load(args.ledger, args.start, args.end)
        out = write_report(ledger, args.out)
    except ReportError as e:
        print(f"gbpd-report: error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out} ({len(ledger.claims)} resolved claims, {args.start} to {args.end})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
