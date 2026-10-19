import asyncio
from datetime import timedelta

import dspy
from dspy.utils import DummyLM
from mcp import Client
from test_mcp_server import SPIKE, START, service  # noqa: F401  (shared ledger fixture)

from gb_power_data.agent.dispatch_agent import claim_days
from gb_power_data.mcp_server.server import build_server


def run(service, days, answers):  # noqa: F811
    """Run the agent with a scripted language model, so the test needs no API key."""

    async def go():
        async with Client(build_server(service)) as client:
            with dspy.context(lm=DummyLM(answers)):
                return await claim_days(client, days)

    return asyncio.run(go())


def test_in_scope_day_is_claimed_then_resolved_by_calibra(service):  # noqa: F811
    day = str(START + timedelta(days=37))
    script = [
        {
            "next_thought": "check scope",
            "next_tool_name": "scope_check",
            "next_tool_args": {"day": day},
        },
        {
            "next_thought": "in scope, claim",
            "next_tool_name": "make_claim",
            "next_tool_args": {"day": day},
        },
        {"next_thought": "done", "next_tool_name": "finish", "next_tool_args": {}},
        {
            "reasoning": "claimed",
            "decision": "claimed",
            "claim_id": f"pillswood-agent-{day}",
            "diagnosis": "in scope",
        },
    ]
    [result] = run(service, [day], script)
    assert [c["tool"] for c in result["tool_calls"]] == ["scope_check", "make_claim"]
    assert result["resolution"]["resolved"] is True  # Calibra resolved it, not the agent
    assert service.ledger_report("agent-dispatch", START, START + timedelta(60))["claims"] == 1


def test_flagged_day_is_investigated_and_can_be_declined(service):  # noqa: F811
    day = str(SPIKE + timedelta(days=2))
    script = [
        {
            "next_thought": "check scope",
            "next_tool_name": "scope_check",
            "next_tool_args": {"day": day},
        },
        {
            "next_thought": "flagged on max_price, get facts",
            "next_tool_name": "explain_flag",
            "next_tool_args": {"feature": "max_price", "day": day},
        },
        {
            "next_thought": "never seen in learning",
            "next_tool_name": "finish",
            "next_tool_args": {},
        },
        {
            "reasoning": "0 learning days as extreme",
            "decision": "declined",
            "claim_id": "none",
            "diagnosis": "missing data: 0 learning days had a max price this high",
        },
    ]
    [result] = run(service, [day], script)
    assert [c["tool"] for c in result["tool_calls"]] == ["scope_check", "explain_flag"]
    assert result["decision"] == "declined" and "resolution" not in result


def test_agent_is_not_given_tools_to_grade_itself(service):  # noqa: F811
    async def names():
        async with Client(build_server(service)) as client:
            listed = await client.list_tools()
            return {t.name for t in listed.tools}

    from gb_power_data.agent.dispatch_agent import AGENT_TOOLS

    assert set(AGENT_TOOLS) < asyncio.run(names())
    assert "resolve" not in AGENT_TOOLS and "ledger_report" not in AGENT_TOOLS
