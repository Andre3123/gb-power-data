"""A DSPy ReAct agent that makes Pillswood's day-ahead claims through Calibra's MCP tools.

The agent may only check scope, read the evidence behind a flag, and claim. It cannot resolve
or score its own claims: that is Calibra's job, done after the agent has committed.

Run: uv run gbpd-agent 2025-03-12 2025-03-13
Needs CALIBRA_LM (a LiteLLM model string, e.g. "anthropic/<model>" or "openai/<model>")
and that provider's API key in the environment (e.g. ANTHROPIC_API_KEY or OPENAI_API_KEY).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import dspy
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment

AGENT_TOOLS = ("scope_check", "explain_flag", "make_claim")  # no resolve: no self-grading
RUNS = Path("data/agent_runs.jsonl")


class ClaimDay(dspy.Signature):
    """You plan the Pillswood battery (98 MW / 196 MWh, Great Britain) for one settlement day.

    1. Call scope_check for the day.
    2. If it is in scope, call make_claim for the day.
    3. If it is flagged, call explain_flag for each flagged feature, passing the day. Name the
       most likely cause as one of: missing data (epistemic), a model limit, or market
       randomness (aleatoric). Use the "history" field of explain_flag: "never seen" or "rare"
       means missing data, whatever the market looks like. Then either decline, or call
       make_claim with a reason that cites the facts (numbers from the tool outputs).
    Never invent numbers. Every number you mention must come from a tool output."""

    day: str = dspy.InputField(desc="settlement day, YYYY-MM-DD")
    decision: Literal["claimed", "declined"] = dspy.OutputField()
    claim_id: str = dspy.OutputField(desc="claim_id returned by make_claim, or 'none'")
    diagnosis: str = dspy.OutputField(
        desc="'in scope', or the likely cause type and the facts it rests on, in two sentences"
    )


async def claim_days(client: Client, days: list[str]) -> list[dict]:
    """Let the agent decide each day, then have Calibra resolve what it claimed."""
    listed = await client.list_tools()
    tools = [dspy.Tool.from_mcp_tool(client, t) for t in listed.tools if t.name in AGENT_TOOLS]
    agent = dspy.ReAct(ClaimDay, tools=tools, max_iters=8)
    results = []
    for day in days:
        pred = await agent.acall(day=day)
        calls = [
            {"tool": v, "args": pred.trajectory.get(f"tool_args_{k.split('_')[-1]}")}
            for k, v in pred.trajectory.items()
            if k.startswith("tool_name_") and v != "finish"
        ]
        result = {
            "day": day,
            "decision": pred.decision,
            "claim_id": pred.claim_id,
            "diagnosis": pred.diagnosis,
            "tool_calls": calls,
        }
        if pred.decision == "claimed" and pred.claim_id != "none":  # Calibra checks, not the agent
            resolved = await client.call_tool("resolve", {"claim_id": pred.claim_id})
            result["resolution"] = resolved.structured_content
        results.append(result)
    return results


async def _run(days: list[str]) -> list[dict]:
    # the server gets a minimal environment plus the model name (never the API key)
    env = {**get_default_environment(), "CALIBRA_LM": os.environ["CALIBRA_LM"]}
    server = StdioServerParameters(command="uv", args=["run", "gbpd-mcp"], env=env)
    async with Client(server) as client:
        return await claim_days(client, days)


def main() -> None:
    model = os.environ.get("CALIBRA_LM")
    if not model:
        sys.exit('set CALIBRA_LM first, e.g. export CALIBRA_LM="anthropic/<model name>"')
    dspy.configure(lm=dspy.LM(model, temperature=0.0))
    results = asyncio.run(_run(sys.argv[1:]))
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    with RUNS.open("a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"run_at": stamp, "model": model, **r}) + "\n")
            print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
