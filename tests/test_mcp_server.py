import asyncio
from datetime import date, timedelta

import pytest
from mcp import Client
from test_dayahead import DAY_SHAPE, SITE, price_table

from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger
from gb_power_data.mcp_server.server import build_server
from gb_power_data.mcp_server.service import CalibraService

START = date(2025, 1, 1)
LEARN_END = START + timedelta(days=35)
SPIKE = START + timedelta(days=40)
SCALES = (0.3, 0.6, 1.0)  # spreads 33, 66, 110: calm, normal, wild


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """36 learning days (12 of each day type) resolved in the ledger, then a few more days."""
    days = {}
    for i in range(45):
        d = START + timedelta(days=i)
        shape = DAY_SHAPE * SCALES[i % 3] * (1 + 0.01 * (i % 4))
        days[d] = shape * 10 if d == SPIKE else shape
    prices = price_table(days)
    path = tmp_path_factory.mktemp("mcp") / "ledger.duckdb"
    ledger = Ledger(path)
    backtest(prices, START + timedelta(days=2), LEARN_END, SITE, ledger, "t")
    ledger.close()
    return CalibraService(prices, path, START, LEARN_END, SITE, "t")


def test_scope_check_passes_a_normal_day_and_flags_a_spike(service):
    assert service.scope_check(START + timedelta(days=38))["in_scope"] is True
    spiky = service.scope_check(SPIKE + timedelta(days=2))  # D-2 is the spike
    assert spiky["in_scope"] is False
    assert any(f.startswith("max_price") for f in spiky["flags"])


def test_make_claim_uses_its_day_type_width_and_is_written_once(service):
    day = START + timedelta(days=38)  # D-2 = day 36: scale index 0, calm
    first = service.make_claim(day)
    again = service.make_claim(day)
    assert first["new"] is True and again["new"] is False
    assert first == {**again, "new": True}
    width = first["upper_gbp"] - first["lower_gbp"]
    assert width == pytest.approx(2 * service.widths()[first["day_type"]], abs=0.02)


def test_resolve_scores_the_claim_and_repeats_the_same_answer(service):
    claim = service.make_claim(START + timedelta(days=39))
    first = service.resolve(claim["claim_id"])
    again = service.resolve(claim["claim_id"])
    assert first["resolved"] is True
    assert first == again  # same truth data: same resolution version, no new row
    assert first["resolution_version"] == 1


def test_ledger_report_matches_the_learning_backtest(service):
    report = service.ledger_report("persistence-dispatch", START, LEARN_END)
    assert report["claims"] == 34  # days 2..35
    assert set(report["by_class"]) == {"calm", "normal", "wild"}
    assert 0 <= report["coverage"] <= 1


def test_explain_flag_gives_facts_and_rejects_unknown_features(service):
    facts = service.explain_flag("max_price", SPIKE + timedelta(days=2))
    low, high = facts["learned_band"]
    assert low <= high
    assert facts["day"]["value"] > high
    assert facts["day"]["learning_days_as_extreme"] == 0  # nothing like it was ever seen
    assert facts["day"]["history"].startswith("never seen: lack of data")
    with pytest.raises(ValueError, match="unknown feature"):
        service.explain_flag("wind")


def test_mcp_client_sees_the_five_tools_and_can_call_one(service):
    async def talk():
        async with Client(build_server(service)) as client:
            tools = await client.list_tools()
            result = await client.call_tool("scope_check", {"day": str(SPIKE + timedelta(2))})
            return {t.name for t in tools.tools}, result

    names, result = asyncio.run(talk())
    assert names == {"scope_check", "make_claim", "resolve", "ledger_report", "explain_flag"}
    assert result.structured_content["in_scope"] is False


def test_a_flagged_day_can_be_claimed_and_the_reason_is_kept(service):
    day = SPIKE + timedelta(days=2)  # D-2 is the spike: flagged
    claim = service.make_claim(day, reason="spike was a one-off outage; wind forecast normal")
    assert claim["in_scope"] is False  # still claimed: ScopeGate warns, it does not forbid
    assert claim["override_reason"].startswith("spike was a one-off")
    service.resolve(claim["claim_id"])
    report = service.ledger_report("agent-dispatch", START, SPIKE + timedelta(days=4))
    assert report["by_scope"]["flagged_with_reason"]["claims"] == 1


def test_claims_record_which_ai_model_made_them(service):
    named = CalibraService(
        service.prices, service.ledger_path, START, LEARN_END, SITE, "t", agent_model="test/m1"
    )
    claim = named.make_claim(START + timedelta(days=41))
    assert claim["model_version"] == "agent:test/m1"


def test_explain_flag_never_uses_claims_resolved_after_the_claim_is_made(tmp_path):
    days = {START + timedelta(days=i): DAY_SHAPE * SCALES[i % 3] for i in range(45)}
    prices = price_table(days)
    path = tmp_path / "ledger.duckdb"
    ledger = Ledger(path)
    backtest(prices, START + timedelta(days=2), LEARN_END, SITE, ledger, "t", "persistence-gate")
    ledger.close()
    svc = CalibraService(prices, path, START, LEARN_END, SITE, "t")
    day = START + timedelta(days=10)
    facts = svc.explain_flag("max_price", day)
    assert facts["history_until"] == str(day - timedelta(days=2))
    assert facts["in_scope"]["claims"] == 7  # gated claims for days 2..8 only
