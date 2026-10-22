import json
from datetime import date, timedelta

import dspy
from dspy.utils import DummyLM
from test_mcp_server import LEARN_END, SPIKE, START, service  # noqa: F401  (shared fixture)

from gb_power_data.agent.tune import FlaggedDay, compare, flagged_days, score
from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger


def day(i: int, lower: float, realised: float) -> FlaggedDay:
    return FlaggedDay(date(2025, 1, 1) + timedelta(days=i), "c", "{}", lower, realised)


def test_score_counts_money_on_claimed_days_only():
    days = [day(0, 100, 150), day(1, 100, 40), day(2, -50, -80)]  # held, broke by 60, broke by 30
    s = score(days, ["claim", "decline", "claim"])
    assert s["claimed"] == 2 and s["right"] == 2  # day 1 declined rightly, day 2 not
    assert s["promised_gbp"] == 100  # a negative lower bound promises nothing
    assert s["shortfall_gbp"] == 30
    assert s["broken_claimed"] == 1 and s["broken_avoided"] == 1


def test_flagged_days_use_only_evidence_known_before_the_day(service):  # noqa: F811
    ledger = Ledger(service.ledger_path)
    end = START + timedelta(days=44)
    backtest(
        service.prices,
        LEARN_END + timedelta(days=1),
        end,
        service.site,
        ledger,
        "t",
        "persistence-gate",
        service.widths(),
        gate=service.gate,
    )
    ledger.close()
    days = flagged_days(service, START, end)
    spike = [d for d in days if d.day == SPIKE + timedelta(days=2)]
    assert spike, "the day planned from the spike must be flagged"
    facts = json.loads(spike[0].evidence)
    assert facts["max_price"]["history_until"] == str(spike[0].day - timedelta(days=2))


def test_compare_runs_all_four_policies_without_an_api_key():
    days = [day(i, 100, 150 if i % 3 else 40) for i in range(12)]
    answers = [{"reasoning": "r", "decision": "claim", "reason": "x"}] * 200
    with dspy.context(lm=DummyLM(answers)):
        table, _ = compare(days[:6], days[6:])
    assert list(table.index) == ["always claim", "always decline", "agent, untuned", "agent, tuned"]
    assert table.loc["always decline", "shortfall_gbp"] == 0
    assert table.loc["always claim", "broken_claimed"] == 2