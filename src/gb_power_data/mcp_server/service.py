"""Calibra's tools as plain Python: what the MCP server exposes to an agent.

Kept apart from the MCP plumbing so every tool can be tested without a server.
The ledger is opened and closed on each call, so other programs can use it in between.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from gb_power_data.calibration.split_conformal import mondrian_half_widths, residuals_by_class
from gb_power_data.claims.dayahead import (
    UK,
    day_prices,
    fingerprint,
    make_claim,
    plan_revenue,
    spread_class,
    uk_midnight,
)
from gb_power_data.ledger.models import Claim
from gb_power_data.ledger.scoring import interval_score, is_covered
from gb_power_data.ledger.store import Ledger
from gb_power_data.optimise.dispatch import Site
from gb_power_data.scopegate.gate import FEATURES, learn_gate

AGENT_LABEL = "agent"  # claims made through MCP are filed as agent_id "agent-dispatch"
LEARNING_AGENT = "persistence-dispatch"  # whose past mistakes set the ranges
GATED_AGENT = "persistence-gate-dispatch"  # whose flagged history explains a flag


class CalibraService:
    def __init__(
        self,
        prices: pd.DataFrame,
        ledger_path: str | Path,
        learn_start: date,
        learn_end: date,
        site: Site,
        code_version: str,
    ) -> None:
        self.prices = prices
        self.ledger_path = ledger_path
        self.learn_start, self.learn_end = learn_start, learn_end
        self.site = site
        self.code_version = code_version
        self.gate = learn_gate(prices, learn_start, learn_end)
        self._widths: dict[str, float] | None = None

    def widths(self) -> dict[str, float]:
        """Mondrian half-widths, learned once from the learning period's resolved claims."""
        if self._widths is None:
            ledger = Ledger(self.ledger_path)
            try:
                by_class = residuals_by_class(
                    ledger, LEARNING_AGENT, self.learn_start, self.learn_end
                )
            finally:
                ledger.close()
            self._widths = mondrian_half_widths(by_class, 0.90)
        return self._widths

    def scope_check(self, day: date) -> dict:
        flags = self.gate.flags(day)
        return {"day": str(day), "in_scope": not flags, "flags": flags}

    def make_claim(self, day: date, reason: str | None = None) -> dict:
        """Write the claim for day D (once). Asking again returns the stored claim.

        A flagged day is still claimed: ScopeGate warns, it does not forbid. The reason for
        going ahead is stored with the claim, so the override itself can be scored later.
        """
        widths = self.widths()
        claim_id = f"pillswood-{AGENT_LABEL}-{day}"
        ledger = Ledger(self.ledger_path)
        try:
            try:
                return _summary(ledger.get_claim(claim_id), new=False)
            except KeyError:
                pass
            claim = make_claim(
                self.prices,
                day,
                self.site,
                self.code_version,
                AGENT_LABEL,
                widths,
                self.gate.flags(day),
            )
            if claim is None:
                raise ValueError(f"cannot claim {day}: forecast day missing or a clock-change day")
            if not claim.context["in_scope"]:
                claim = claim.model_copy(
                    update={"context": {**claim.context, "override_reason": reason}}
                )
            ledger.add_claim(claim)
            return _summary(claim, new=True)
        finally:
            ledger.close()

    def resolve(self, claim_id: str) -> dict:
        """Check a claim against what really happened, if the real prices are known."""
        ledger = Ledger(self.ledger_path)
        try:
            claim = ledger.get_claim(claim_id)
            d = claim.target_start.astimezone(UK).date()
            actual = day_prices(self.prices, d)
            if actual.empty:
                return {"claim_id": claim_id, "resolved": False, "reason": f"no prices for {d}"}
            net = np.array(claim.context["plan_net_mw"])
            realised = plan_revenue(net, actual, self.site.dt_h)
            res = ledger.resolve(claim_id, realised, fingerprint(actual), spread_class(actual))
            return {
                "claim_id": claim_id,
                "resolved": True,
                "resolution_version": res.resolution_version,
                "realised_gbp": round(realised, 2),
                "covered": is_covered(claim, res),
                "interval_score": round(interval_score(claim, res), 2),
            }
        finally:
            ledger.close()

    def ledger_report(self, agent_id: str, start: date, end: date) -> dict:
        """Track record of one agent: how often its promise held, and its average bill."""
        df = self._resolved(agent_id, start, end)
        if df.empty:
            return {"agent_id": agent_id, "claims": 0}
        by_class = {
            cls: {"claims": len(g), "coverage": round(g["covered"].mean(), 3)}
            for cls, g in df.groupby("class")
        }
        by_scope = {
            name: {"claims": len(g), "coverage": round(g["covered"].mean(), 3)}
            for name, g in df.groupby("scope")
        }
        return {
            "agent_id": agent_id,
            "claims": len(df),
            "coverage": round(df["covered"].mean(), 3),
            "promised": round(df["coverage"].mean(), 3),
            "mean_interval_score": round(df["score"].mean(), 2),
            "by_class": by_class,
            "by_scope": by_scope,
        }

    def explain_flag(self, feature: str, day: date | None = None) -> dict:
        """Ledger evidence for one ScopeGate feature. Facts only: causes are for the agent."""
        if feature not in FEATURES:
            raise ValueError(f"unknown feature {feature!r}; choose from {', '.join(FEATURES)}")
        df = self._resolved(GATED_AGENT, date(1900, 1, 1), date(2999, 12, 31))
        evidence: dict = {
            "feature": feature,
            "learned_band": [round(self.gate.low[feature], 2), round(self.gate.high[feature], 2)],
            "history_agent": GATED_AGENT,
        }
        if not df.empty:
            total_bill = df["score"].sum()
            hit = df["flags"].apply(lambda fl: any(f.startswith(feature) for f in fl or []))
            for name, part in (("flagged_on_feature", df[hit]), ("in_scope", df[df["in_scope"]])):
                evidence[name] = {
                    "claims": len(part),
                    "coverage": round(part["covered"].mean(), 3) if len(part) else None,
                    "share_of_bill": round(part["score"].sum() / total_bill, 3),
                }
        if day is not None:
            evidence["day"] = self._compare_to_learning(feature, day)
        evidence["note"] = "Facts from the ledger; hypotheses about causes must cite them."
        return evidence

    def _compare_to_learning(self, feature: str, day: date) -> dict:
        """How many learning days were at least as extreme as this day's D-2 value."""
        stats = self.gate.stats
        source = day - pd.Timedelta(days=2)
        if source not in stats.index or pd.isna(stats.loc[source, feature]):
            return {"value": None, "learning_days_as_extreme": None}
        value = float(stats.loc[source, feature])
        learning = stats.loc[
            (stats.index >= self.learn_start) & (stats.index <= self.learn_end), feature
        ].dropna()
        median = learning.median()
        as_extreme = (learning >= value) if value >= median else (learning <= value)
        count = int(as_extreme.sum())
        return {
            "value": round(value, 2),
            "learning_days": len(learning),
            "learning_days_as_extreme": count,
            "history": _history_label(count, len(learning)),
        }

    def _resolved(self, agent_id: str, start: date, end: date) -> pd.DataFrame:
        """One agent's claims with their latest resolution, scored."""
        ledger = Ledger(self.ledger_path)
        try:
            df = ledger.con.execute(
                """
                SELECT c.reference_class AS class, c.lower, c.upper, c.coverage, c.context,
                       r.realised_value AS truth
                FROM claims c
                JOIN resolutions r ON r.claim_id = c.claim_id
                WHERE c.agent_id = ?
                  AND c.target_start >= ? AND c.target_start < ?
                  AND r.resolution_version = (
                      SELECT max(resolution_version) FROM resolutions WHERE claim_id = c.claim_id
                  )
                """,
                [agent_id, _utc(start), _utc(end) + pd.Timedelta(days=1)],
            ).df()
        finally:
            ledger.close()
        if df.empty:
            return df
        outside = (df["lower"] - df["truth"]).clip(lower=0) + (df["truth"] - df["upper"]).clip(
            lower=0
        )
        df["covered"] = outside == 0
        df["score"] = (df["upper"] - df["lower"]) + 2 / (1 - df["coverage"]) * outside
        context = df["context"].map(json.loads)
        df["flags"] = context.map(lambda c: c.get("scope_flags"))
        df["in_scope"] = context.map(lambda c: c.get("in_scope", True))
        df["scope"] = context.map(_scope_label)
        return df


def _history_label(count: int, total: int) -> str:
    """How much past evidence exists for a value this extreme (sets the uncertainty type)."""
    if count == 0:
        return "never seen: lack of data (epistemic)"
    if count <= 0.01 * total:
        return "rare: little data (mostly epistemic)"
    return "seen before: enough data (randomness is the likelier cause)"


def _scope_label(context: dict) -> str:
    """in scope / flagged, went ahead with a reason / flagged, went ahead without one."""
    if context.get("in_scope", True):
        return "in_scope"
    return "flagged_with_reason" if context.get("override_reason") else "flagged_no_reason"


def _summary(claim: Claim, new: bool) -> dict:
    return {
        "claim_id": claim.claim_id,
        "new": new,
        "day": str(claim.target_start.astimezone(UK).date()),
        "point_gbp": round(claim.point, 2),
        "lower_gbp": round(claim.lower, 2),
        "upper_gbp": round(claim.upper, 2),
        "coverage": claim.coverage,
        "day_type": claim.reference_class,
        "in_scope": claim.context.get("in_scope", True),
        "scope_flags": claim.context.get("scope_flags"),
        "override_reason": claim.context.get("override_reason"),
    }


def _utc(d: date):
    return uk_midnight(d).replace(tzinfo=None)