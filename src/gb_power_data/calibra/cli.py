"""calibra: verify any forecaster's claims. Bring your own forecasts, get honest ranges.

  calibra template DIR                 write example forecasts.csv and outcomes.csv
  calibra init YOUR_FILE.csv           draft calibra.toml: which of your columns is which
  calibra replay FORECASTS OUTCOMES    run a past track record through Calibra, in time order
  calibra submit FORECASTS             record new forecasts (before their target period)
  calibra resolve OUTCOMES             score recorded forecasts against what happened
  calibra report AGENT                 track record: honesty of both ranges, in money too
  calibra status AGENT                 current range method and width, and the scope bands
  calibra verify                       check the ledger's hash chain
  calibra serve                        run the HTTPS API and web app (hosted Calibra)
  calibra customer create NAME         hosted: a new customer and their API key (shown once)

Add --map calibra.toml to replay, submit and resolve to read your own files as they are.
The ledger is data/calibra/ledger.duckdb, or the file in CALIBRA_LEDGER, or --ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from gb_power_data.calibra.csvio import CsvError, read_forecasts, read_outcomes, write_templates
from gb_power_data.calibra.engine import Calibra, Settings
from gb_power_data.calibra.mapping import Mapping, MappingError, draft, read_table
from gb_power_data.ledger.store import Ledger

DEFAULT_LEDGER = Path("data/calibra/ledger.duckdb")


def _code_version() -> str:
    try:
        from gb_power_data.claims.run_backtest import git_commit

        return git_commit()
    except Exception:  # not a git checkout: still usable
        return "unknown"


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _when(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError(f"'{text}' needs a UTC offset, e.g. +00:00")
    return dt


def _home(args: argparse.Namespace) -> Path:
    return args.home or Path(os.environ.get("CALIBRA_HOME", "data/calibra_server"))


def _forecasts(args: argparse.Namespace):
    if args.map:
        return Mapping.load(args.map).forecasts(read_table(args.forecasts))
    return read_forecasts(args.forecasts)


def _outcomes(args: argparse.Namespace, path: Path):
    if args.map:
        return Mapping.load(args.map).outcomes_from(read_table(path))
    return read_outcomes(path)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibra",
        description=__doc__.split("\n\n")[0],
        epilog=__doc__.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ledger", type=Path, help="ledger file (default: CALIBRA_LEDGER or "
                   f"{DEFAULT_LEDGER})")  # fmt: skip
    p.add_argument("--coverage", type=float, default=0.90, help="promised coverage, default 0.90")
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("template", help="write example CSV files")
    t.add_argument("folder", type=Path)
    i = sub.add_parser("init", help="draft calibra.toml from your own forecast file")
    i.add_argument("file", type=Path)
    i.add_argument("--agent", default="my-optimiser", help="your agent or optimiser name")
    i.add_argument("--out", type=Path, default=Path("calibra.toml"))
    r = sub.add_parser("replay", help="run a past track record through Calibra")
    r.add_argument("forecasts", type=Path)
    r.add_argument("outcomes", type=Path)
    s = sub.add_parser("submit", help="record new forecasts")
    s.add_argument("forecasts", type=Path)
    s.add_argument("--reason", help="why to go ahead if a forecast is out of scope")
    o = sub.add_parser("resolve", help="score recorded forecasts")
    o.add_argument("outcomes", type=Path)
    for cmd in (r, s, o):
        cmd.add_argument("--map", type=Path, help="calibra.toml: read your own columns")
    rep = sub.add_parser("report", help="an agent's track record")
    rep.add_argument("agent")
    st = sub.add_parser("status", help="what Calibra knows about an agent now")
    st.add_argument("agent")
    st.add_argument("--as-of", type=_when, default=None, help="ISO time with offset; default now")
    st.add_argument("--day-type")
    sub.add_parser("verify", help="check the ledger's hash chain")
    sv = sub.add_parser("serve", help="run the API and web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    sv.add_argument("--home", type=Path, help="server data folder (default: CALIBRA_HOME)")
    cu = sub.add_parser("customer", help="hosted: manage customers and their keys")
    cu.add_argument("action", choices=["create", "rotate", "list"])
    cu.add_argument("name", nargs="?")
    cu.add_argument("--home", type=Path, help="server data folder (default: CALIBRA_HOME)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ledger = args.ledger or Path(os.environ.get("CALIBRA_LEDGER", DEFAULT_LEDGER))
    engine = Calibra(ledger, Settings(coverage=args.coverage, code_version=_code_version()))
    try:
        if args.command == "template":
            for path in write_templates(args.folder):
                print(f"wrote {path}")
        elif args.command == "init":
            if args.out.exists():
                raise ValueError(f"{args.out} exists; remove it or pass --out another name")
            args.out.write_text(draft(read_table(args.file), args.agent), encoding="utf-8")
            print(f"wrote {args.out}: check every line marked CHECK, then run replay --map")
        elif args.command == "replay":
            result = engine.replay(_forecasts(args), _outcomes(args, args.outcomes))
            _print({"ledger": str(ledger), **result})
        elif args.command == "submit":
            receipts = [engine.submit(f, args.reason) for f in _forecasts(args)]
            flagged = sum(not r["in_scope"] for r in receipts)
            print(f"{len(receipts)} forecasts, {sum(r['new'] for r in receipts)} new, "
                  f"{flagged} out of scope")  # fmt: skip
            _print(receipts)
        elif args.command == "resolve":
            done = [engine.resolve(o) for o in _outcomes(args, args.outcomes)]
            covered = sum(d["covered"] for d in done if d["calibrated"])
            print(f"{len(done)} resolved; {covered} of {sum(d['calibrated'] for d in done)} "
                  "calibrated ranges held")  # fmt: skip
            _print(done)
        elif args.command == "report":
            _print(engine.report(args.agent))
        elif args.command == "status":
            _print(engine.status(args.agent, args.as_of or datetime.now(UTC), args.day_type))
        elif args.command == "serve":
            import uvicorn

            from gb_power_data.calibra.api import create_app

            uvicorn.run(create_app(_home(args), _code_version()), host=args.host, port=args.port)
        elif args.command == "customer":
            from gb_power_data.calibra.tenants import Tenants

            tenants = Tenants(_home(args))
            if args.action == "list":
                print("\n".join(tenants.names()) or "no customers yet")
            elif not args.name:
                raise ValueError(f"customer {args.action} needs a NAME")
            else:
                make = tenants.create if args.action == "create" else tenants.rotate
                key = make(args.name)
                print(f"customer: {args.name}\nAPI key (shown once, store it safely): {key}")
        elif args.command == "verify":
            if not ledger.is_file():
                raise ValueError(f"ledger file not found: {ledger}")
            led = Ledger(ledger)
            try:
                rep = led.verify()
            finally:
                led.close()
            print(f"chain entries: {rep.entries}; head: {rep.head}")
            print("OK" if rep.ok else "BROKEN:\n  " + "\n  ".join(rep.problems[:20]))
            return 0 if rep.ok else 1
    except (CsvError, MappingError, ValueError, KeyError) as e:
        print(f"calibra: error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
