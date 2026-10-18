"""Calibra MCP server: lets any MCP client (an agent, Claude, an IDE) use Calibra's tools.

Run: uv run gbpd-mcp     (talks over stdin/stdout; an MCP client starts it for you)
The docstrings below are what the agent reads to decide which tool to call.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from mcp.server.mcpserver import MCPServer

from gb_power_data.claims.run_backtest import LEDGER, git_commit
from gb_power_data.mcp_server.service import CalibraService
from gb_power_data.optimise.run_day import PILLSWOOD, PRICES

LEARN_START, LEARN_END = date(2023, 1, 1), date(2024, 12, 31)

INSTRUCTIONS = """Calibra verifies day-ahead revenue claims for the Pillswood battery (GB).
Before claiming a day, call scope_check. If it is flagged, call explain_flag for each flagged
feature before deciding. A flagged day can still be claimed: pass a reason that cites the
evidence. make_claim writes a claim once; resolve checks it against reality.
Dates are YYYY-MM-DD. Money is GBP."""


def build_server(service: CalibraService) -> MCPServer:
    mcp = MCPServer(name="calibra", instructions=INSTRUCTIONS)

    @mcp.tool()
    def scope_check(day: str) -> dict[str, Any]:
        """Is day D like the days Calibra learned from? Returns in_scope and the reasons if not.
        Uses only data up to D-2, as the agent would have it."""
        return service.scope_check(date.fromisoformat(day))

    @mcp.tool()
    def make_claim(day: str, reason: str | None = None) -> dict[str, Any]:
        """Write Pillswood's day-ahead revenue claim for day D to the ledger: a point, a 90%
        range learned from past mistakes, the day type and ScopeGate flags. Written once:
        asking again returns the stored claim. If the day is flagged, give a reason for going
        ahead (citing explain_flag facts); it is stored and the override is scored later."""
        return service.make_claim(date.fromisoformat(day), reason)

    @mcp.tool()
    def resolve(claim_id: str) -> dict[str, Any]:
        """Check a claim against the real prices: realised revenue, covered or not, and the
        interval score (lower is better). Same data twice gives the same answer."""
        return service.resolve(claim_id)

    @mcp.tool()
    def ledger_report(agent_id: str, start: str, end: str) -> dict[str, Any]:
        """Track record of an agent between two dates: claims, coverage vs promise, mean
        interval score, coverage by day type. Example agent_id: persistence-mcp-dispatch."""
        return service.ledger_report(agent_id, date.fromisoformat(start), date.fromisoformat(end))

    @mcp.tool()
    def explain_flag(feature: str, day: str | None = None) -> dict[str, Any]:
        """Ledger evidence for a ScopeGate feature (spread, max_price, min_price,
        volatility_7d): its learned normal band, how past flagged claims performed, and, if a
        day is given, how many learning days were as extreme. Facts only, no causes."""
        return service.explain_flag(feature, date.fromisoformat(day) if day else None)

    return mcp


def main() -> None:
    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date
    service = CalibraService(prices, LEDGER, LEARN_START, LEARN_END, PILLSWOOD, git_commit())
    build_server(service).run("stdio")


if __name__ == "__main__":
    main()
