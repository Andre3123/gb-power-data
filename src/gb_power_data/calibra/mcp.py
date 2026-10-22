"""calibra-mcp: Calibra's tools for any AI agent, over the Model Context Protocol.

The agent can check scope, submit forecasts and read its own track record. It cannot record
outcomes: those come from the operator's meter or settlement data (calibra resolve), so an
agent never grades itself.

Run: uv run calibra-mcp   (an MCP client starts it)
Local: records go to the ledger file in CALIBRA_LEDGER (default data/calibra/ledger.duckdb).
Hosted: set CALIBRA_API_URL and CALIBRA_KEY and the tools call your hosted Calibra instead.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from gb_power_data.calibra.cli import DEFAULT_LEDGER, _code_version
from gb_power_data.calibra.engine import Calibra, Settings
from gb_power_data.calibra.remote import RemoteEngine
from gb_power_data.calibra.schema import Forecast

INSTRUCTIONS = """Calibra records your forecasts before the event and gives each one an honest
range, learned from your own past errors. Workflow: call check_scope with the inputs you used;
if it is flagged, decide whether to go ahead and say why. Then call submit_forecast once per
forecast. Use calibration_status to see how your ranges are learned and agent_report to see
your track record. Times are ISO 8601 with a UTC offset. You cannot record outcomes."""


def _when(text: str | None) -> datetime:
    if not text:
        return datetime.now(UTC)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"'{text}' needs a UTC offset, e.g. 2025-01-08T00:00:00+00:00")
    return dt


def _answer(work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a tool; a problem the agent can fix comes back as a plain message, not a crash."""
    try:
        return work()
    except ValidationError as e:
        reasons = "; ".join(
            f"{'.'.join(str(x) for x in err['loc']) or 'forecast'}: {err['msg']}"
            for err in e.errors()
        )
        raise ToolError(f"forecast not accepted: {reasons}") from e
    except (ValueError, KeyError) as e:
        raise ToolError(str(e)) from e


def build_server(engine: Calibra | RemoteEngine) -> MCPServer:
    mcp = MCPServer(name="calibra", instructions=INSTRUCTIONS)

    @mcp.tool()
    def check_scope(
        agent_id: str, features: dict[str, float], as_of: str | None = None
    ) -> dict[str, Any]:
        """Are these inputs like the ones your past forecasts were made from? Returns in_scope
        and, if not, one reason per feature (outside its learned 1-99% band, missing, or never
        seen). Nothing is recorded."""
        return _answer(lambda: engine.check_scope(agent_id, _when(as_of), features))

    @mcp.tool()
    def submit_forecast(
        agent_id: str,
        forecast_id: str,
        target_start: str,
        target_end: str,
        point: float,
        features: dict[str, float] | None = None,
        day_type: str | None = None,
        lower: float | None = None,
        upper: float | None = None,
        asset: dict[str, float | str] | None = None,
        asset_source: dict[str, str] | None = None,
        target_name: str = "value",
        unit: str = "unit",
        model_version: str = "unknown",
        as_of: str | None = None,
        data_cutoff: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record one forecast before its target period. Calibra adds its own range (90% by
        default) learned from your past errors, checks the inputs (features) for scope, and
        writes it to a tamper-evident ledger. Written once: the same forecast again returns the
        stored record. lower/upper are your own range, if you have one: it is scored too.
        asset holds the physical assumptions you used (e.g. round_trip_efficiency).
        If the forecast is out of scope, give a reason for going ahead."""

        def work() -> dict[str, Any]:
            f = Forecast(
                forecast_id=forecast_id,
                agent_id=agent_id,
                model_version=model_version,
                target_start=_when(target_start),
                target_end=_when(target_end),
                as_of=_when(as_of),
                data_cutoff=_when(data_cutoff) if data_cutoff else None,
                target_name=target_name,
                unit=unit,
                point=point,
                lower=lower,
                upper=upper,
                day_type=day_type,
                features=features or {},
                asset=asset or {},
                asset_source=asset_source or {},
            )
            return engine.submit(f, reason)

        return _answer(work)

    @mcp.tool()
    def calibration_status(agent_id: str, day_type: str | None = None) -> dict[str, Any]:
        """How Calibra would range your next forecast: method (mondrian per day type, pooled,
        or none yet), half-width, how many past outcomes it rests on, and the scope bands."""
        return _answer(lambda: engine.status(agent_id, datetime.now(UTC), day_type))

    @mcp.tool()
    def agent_report(agent_id: str) -> dict[str, Any]:
        """Your track record: how often Calibra's range and your own range held, their widths,
        how often the lower bound was broken and by how much, split by in scope vs flagged."""
        return _answer(lambda: engine.report(agent_id))

    return mcp


def main() -> None:
    url = os.environ.get("CALIBRA_API_URL")
    if url:
        key = os.environ.get("CALIBRA_KEY")
        if not key:
            raise SystemExit("CALIBRA_API_URL is set: also set CALIBRA_KEY to your API key")
        engine: Calibra | RemoteEngine = RemoteEngine(url, key)
    else:
        ledger = Path(os.environ.get("CALIBRA_LEDGER", DEFAULT_LEDGER))
        engine = Calibra(ledger, Settings(code_version=_code_version()))
    build_server(engine).run("stdio")


if __name__ == "__main__":
    main()
