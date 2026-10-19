"""Read the claim ledger (read-only) and compute the numbers shown in the report."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from gb_power_data.claims.dayahead import UK, uk_midnight
from gb_power_data.ledger.models import Claim, Resolution
from gb_power_data.ledger.scoring import interval_score, is_covered
from gb_power_data.ledger.store import TIME_FIELDS

AGENTS = {
    "persistence-dispatch": "Naive (±40%)",
    "persistence-cp-dispatch": "Split conformal",
    "persistence-mcp-dispatch": "Mondrian",
}
DAY_TYPES = ("calm", "normal", "wild")
PROMISED = 0.90
ROLLING_DAYS = 30
TOP_N = 10

RESOLVED_QUERY = f"""
SELECT c.*, r.resolution_version, r.resolved_at, r.realised_value, r.realised_class,
       r.truth_data_hash
FROM claims c
JOIN (
    SELECT * FROM resolutions
    QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY resolution_version DESC) = 1
) r ON r.claim_id = c.claim_id
WHERE c.agent_id IN ({", ".join("?" for _ in AGENTS)})
  AND c.target_start >= ? AND c.target_start < ?
ORDER BY c.target_start, c.agent_id
"""

UNRESOLVED_QUERY = f"""
SELECT count(*) FROM claims c
WHERE c.agent_id IN ({", ".join("?" for _ in AGENTS)})
  AND c.target_start >= ? AND c.target_start < ?
  AND NOT EXISTS (SELECT 1 FROM resolutions r WHERE r.claim_id = c.claim_id)
"""


class ReportError(Exception):
    """A problem the user can fix (bad input, missing ledger, no data): shown without traceback."""


@dataclass(frozen=True)
class LedgerData:
    path: Path
    start: date
    end: date
    claims: pd.DataFrame  # one row per resolved claim, latest resolution only
    unresolved: int


def _aware(dt: datetime) -> datetime:
    """The ledger stores UTC without a timezone label."""
    return dt.replace(tzinfo=UTC)


def _db_time(d: date) -> datetime:
    return uk_midnight(d).astimezone(UTC).replace(tzinfo=None)


def _to_models(row: dict) -> tuple[Claim, Resolution]:
    claim_fields = {k: row[k] for k in Claim.model_fields}
    for f in TIME_FIELDS:
        claim_fields[f] = _aware(claim_fields[f])
    claim_fields["context"] = json.loads(claim_fields["context"])
    res_fields = {k: row[k] for k in Resolution.model_fields}
    res_fields["resolved_at"] = _aware(res_fields["resolved_at"])
    return Claim(**claim_fields), Resolution(**res_fields)


def load(ledger_path: str | Path, start: date, end: date) -> LedgerData:
    """Resolved claims of the three agents with target_start on UK days start..end (inclusive)."""
    path = Path(ledger_path)
    if not path.is_file():
        raise ReportError(f"ledger file not found: {path}")
    params = [*AGENTS, _db_time(start), _db_time(end + timedelta(days=1))]
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as e:
        raise ReportError(f"could not open ledger {path} read-only: {e}") from e
    try:
        cur = con.execute(RESOLVED_QUERY, params)
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        unresolved = con.execute(UNRESOLVED_QUERY, params).fetchone()[0]
    except duckdb.Error as e:
        raise ReportError(f"could not read claims from {path}: {e}") from e
    finally:
        con.close()
    if not rows:
        extra = f" ({unresolved} claims are not resolved yet)" if unresolved else ""
        raise ReportError(f"no resolved claims between {start} and {end} in {path}{extra}")

    records = []
    for row in rows:
        claim, res = _to_models(row)
        if not math.isclose(claim.coverage, PROMISED):
            raise ReportError(
                f"claim {claim.claim_id} promises {claim.coverage:.0%} coverage; "
                f"this report assumes every claim promises {PROMISED:.0%}"
            )
        records.append(
            {
                "agent": AGENTS[claim.agent_id],
                "claim_id": claim.claim_id,
                "day": claim.target_start.astimezone(UK).date(),
                "day_type": claim.reference_class,
                "lower": claim.lower,
                "upper": claim.upper,
                "realised": res.realised_value,
                "width": claim.upper - claim.lower,
                "covered": is_covered(claim, res),
                "score": interval_score(claim, res),
                "resolution_version": res.resolution_version,
                "code_version": claim.code_version,
            }
        )
    return LedgerData(path, start, end, pd.DataFrame(records), int(unresolved))


def headline(df: pd.DataFrame) -> pd.DataFrame:
    """Per agent: claims, coverage, mean width, mean interval score (all agents listed)."""
    out = df.groupby("agent").agg(
        claims=("covered", "size"),
        coverage=("covered", "mean"),
        mean_width=("width", "mean"),
        mean_score=("score", "mean"),
    )
    out = out.reindex(list(AGENTS.values()))
    out["claims"] = out["claims"].fillna(0).astype(int)
    return out


def coverage_by_day_type(df: pd.DataFrame) -> pd.DataFrame:
    """Rows: agents; columns: (claims|coverage, day type). Empty groups stay as NaN / 0."""
    grouped = df.groupby(["agent", "day_type"])["covered"].agg(claims="size", coverage="mean")
    index = pd.MultiIndex.from_product([list(AGENTS.values()), DAY_TYPES])
    grouped = grouped.reindex(index)
    grouped["claims"] = grouped["claims"].fillna(0).astype(int)
    return grouped


def rolling_coverage(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Per agent: coverage over the last 30 calendar days of target dates, at each claim date."""
    out = {}
    for agent, g in df.groupby("agent"):
        s = g.set_index(pd.to_datetime(g["day"]))["covered"].astype(float).sort_index()
        out[agent] = s.rolling(f"{ROLLING_DAYS}D").mean()
    return out


def daily_score(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Per agent: total interval score of each target day."""
    return {
        agent: g.groupby(pd.to_datetime(g["day"]))["score"].sum().sort_index()
        for agent, g in df.groupby("agent")
    }


def top_days(df: pd.DataFrame, n: int = TOP_N) -> dict[str, pd.DataFrame]:
    """Per agent: the n claims with the highest interval score ("bill")."""
    cols = ["day", "day_type", "lower", "upper", "realised", "score"]
    return {
        agent: g.sort_values(["score", "day"], ascending=[False, True]).head(n)[cols]
        for agent, g in df.groupby("agent")
    }


def code_versions(df: pd.DataFrame) -> list[str]:
    return sorted(df["code_version"].unique())
