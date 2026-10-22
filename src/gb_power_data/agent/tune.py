"""Teach the agent when to claim a flagged day, from the ledger's own outcomes (DSPy optimiser).

On a day ScopeGate flags, the agent must choose: claim (promise the 90% range) or decline.
Calibra's ledger knows, for past flagged days, whether the promise held. A DSPy optimiser
(BootstrapFewShot) turns those past outcomes into worked examples for the agent's prompt.

The test is out of sample and in time order: tune on the earlier flagged days, then compare on
the later ones. The agent only sees what was known the day before: the proposed claim, the
ScopeGate flags and explain_flag's facts (ledger history up to D-2).

Run: uv run gbpd-tune 2025-01-01 2026-09-30
Needs CALIBRA_LM and the provider's API key, as for gbpd-agent.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import dspy
import duckdb
import pandas as pd

from gb_power_data.claims.dayahead import UK
from gb_power_data.mcp_server.service import GATED_AGENT, CalibraService
from gb_power_data.scopegate.gate import FEATURES

TUNED = Path("data/agent_tuned_decide.json")


class Decide(dspy.Signature):
    """ScopeGate flagged this Pillswood battery day (98 MW / 196 MWh, Great Britain): the prices
    two days before look unlike the learning years. Decide whether to claim, which promises the
    owner that revenue will be at least the lower bound, or to decline and promise nothing.
    Claim when the range is likely to hold; decline when revenue is likely to fall below the
    lower bound. Base the decision only on the facts given."""

    day: str = dspy.InputField(desc="settlement day, YYYY-MM-DD")
    claim: str = dspy.InputField(desc="the proposed claim: point and 90% range in GBP, day type")
    evidence: str = dspy.InputField(desc="ScopeGate flags and explain_flag facts, as JSON")
    decision: Literal["claim", "decline"] = dspy.OutputField()
    reason: str = dspy.OutputField(desc="one sentence citing numbers from the evidence")


@dataclass(frozen=True)
class FlaggedDay:
    day: date
    claim: str
    evidence: str
    lower: float
    realised: float

    @property
    def best(self) -> str:
        """What the decision should have been: decline only if the promise broke."""
        return "decline" if self.realised < self.lower else "claim"

    @property
    def promised(self) -> float:
        return max(self.lower, 0.0)

    @property
    def shortfall(self) -> float:
        return max(self.lower - self.realised, 0.0)

    def example(self) -> dspy.Example:
        return dspy.Example(
            day=str(self.day), claim=self.claim, evidence=self.evidence, decision=self.best
        ).with_inputs("day", "claim", "evidence")


def flagged_days(service: CalibraService, start: date, end: date) -> list[FlaggedDay]:
    """Resolved, flagged claims of the gated run, with the evidence known before each day."""
    q = """
    SELECT c.target_start, c.point, c.lower, c.upper, c.reference_class, c.context,
           r.realised_value
    FROM claims c JOIN (
        SELECT * FROM resolutions
        QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY resolution_version DESC) = 1
    ) r USING (claim_id)
    WHERE c.agent_id = ?
    ORDER BY c.target_start
    """
    con = duckdb.connect(str(service.ledger_path), read_only=True)
    try:
        rows = con.execute(q, [GATED_AGENT]).fetchall()
    finally:
        con.close()
    out = []
    for ts, point, lower, upper, cls, ctx, realised in rows:
        d = pd.Timestamp(ts, tz="UTC").tz_convert(UK).date()
        flags = json.loads(ctx).get("scope_flags") or []
        if not (start <= d <= end) or not flags:
            continue
        features = sorted({f.split()[0] for f in flags if f.split()[0] in FEATURES})
        facts = {"flags": flags, **{f: service.explain_flag(f, d) for f in features}}
        claim = (
            f"point GBP {point:,.0f}, 90% range GBP {lower:,.0f} to {upper:,.0f}, day type {cls}"
        )
        out.append(FlaggedDay(d, claim, json.dumps(facts), lower, realised))
    return out


def metric(example: dspy.Example, pred: dspy.Prediction, trace=None) -> bool:
    return pred.decision == example.decision


def score(days: list[FlaggedDay], decisions: list[str]) -> dict:
    """What a list of decisions is worth, in GBP, on days whose outcome is known."""
    claimed = [d for d, x in zip(days, decisions, strict=True) if x == "claim"]
    return {
        "days": len(days),
        "claimed": len(claimed),
        "right": sum(d.best == x for d, x in zip(days, decisions, strict=True)),
        "promised_gbp": round(sum(d.promised for d in claimed)),
        "shortfall_gbp": round(sum(d.shortfall for d in claimed)),
        "broken_claimed": sum(d.shortfall > 0 for d in claimed),
        "broken_avoided": sum(d.shortfall > 0 for d in days)
        - sum(d.shortfall > 0 for d in claimed),
    }


def decide_all(program: dspy.Module, days: list[FlaggedDay]) -> list[str]:
    out = []
    for d in days:
        try:
            out.append(program(day=str(d.day), claim=d.claim, evidence=d.evidence).decision)
        except Exception as e:  # a failed answer counts as a claim (the default), and is shown
            print(f"  {d.day}: no valid answer ({type(e).__name__}), counted as claim")
            out.append("claim")
    return out


def compare(train: list[FlaggedDay], test: list[FlaggedDay]) -> tuple[pd.DataFrame, dspy.Module]:
    """Score four policies on the test days; the tuned one learns from the train days only."""
    untuned = dspy.ChainOfThought(Decide)
    optimiser = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=4, max_labeled_demos=8)
    tuned = optimiser.compile(dspy.ChainOfThought(Decide), trainset=[d.example() for d in train])
    rows = {
        "always claim": score(test, ["claim"] * len(test)),
        "always decline": score(test, ["decline"] * len(test)),
        "agent, untuned": score(test, decide_all(untuned, test)),
        "agent, tuned": score(test, decide_all(tuned, test)),
    }
    return pd.DataFrame(rows).T, tuned


def main() -> None:
    from gb_power_data.claims.run_backtest import LEDGER, git_commit
    from gb_power_data.optimise.run_day import PILLSWOOD, PRICES
    from gb_power_data.physics.run_physics import LEARN_END, LEARN_START

    model = os.environ.get("CALIBRA_LM")
    if not model:
        sys.exit('set CALIBRA_LM first, e.g. export CALIBRA_LM="anthropic/<model name>"')
    start, end = (pd.Timestamp(a).date() for a in sys.argv[1:3])
    dspy.configure(lm=dspy.LM(model, temperature=0.0))

    prices = pd.read_parquet(PRICES)
    prices["settlement_date"] = pd.to_datetime(prices["settlement_date"]).dt.date
    service = CalibraService(prices, LEDGER, LEARN_START, LEARN_END, PILLSWOOD, git_commit())
    days = flagged_days(service, start, end)
    if len(days) < 10:
        sys.exit(f"only {len(days)} flagged days: too few to tune and test")
    half = len(days) // 2
    train, test = days[:half], days[half:]
    print(
        f"{len(days)} flagged days. Tune on {len(train)} ({train[0].day} to {train[-1].day},"
        f" {sum(d.best == 'decline' for d in train)} broke); test on {len(test)}"
        f" ({test[0].day} to {test[-1].day}, {sum(d.best == 'decline' for d in test)} broke)\n"
    )
    table, tuned = compare(train, test)
    print(table.to_string())
    TUNED.parent.mkdir(parents=True, exist_ok=True)
    tuned.save(str(TUNED))
    print(f"\nmodel {model}; tuned program saved to {TUNED}")
    print("right = decisions that match the outcome; shortfall = money missing on claimed days")


if __name__ == "__main__":
    main()