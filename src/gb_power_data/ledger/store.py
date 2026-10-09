"""Append-only claim ledger on duckdb
Guarantee at this stage:
-rows are only ever added: the class has no update or delete method
-a claim _id can be written once;
-resolving twice from the same data returns the existing row instead
-resolving from corrected data adds the next version and keeps the
all times are stored in UTC
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from gb_power_data.ledger.models import Claim, Resolution
from gb_power_data.ledger.scoring import check_pair

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id        VARCHAR PRIMARY KEY,
    agent_id        VARCHAR NOT NULL,
    model_version   VARCHAR NOT NULL,
    code_version    VARCHAR NOT NULL,
    as_of           TIMESTAMP NOT NULL,
    data_cutoff     TIMESTAMP NOT NULL,
    recorded_at     TIMESTAMP NOT NULL,
    target_name     VARCHAR NOT NULL,
    unit            VARCHAR NOT NULL,
    target_start    TIMESTAMP NOT NULL,
    target_end      TIMESTAMP NOT NULL,
    point           DOUBLE NOT NULL,
    lower           DOUBLE NOT NULL,
    upper           DOUBLE NOT NULL,
    coverage        DOUBLE NOT NULL,
    reference_class VARCHAR,
    resolution_rule VARCHAR NOT NULL,
    input_data_hash VARCHAR NOT NULL,
    context         VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS resolutions (
    claim_id           VARCHAR NOT NULL REFERENCES claims (claim_id),
    resolution_version INTEGER NOT NULL,
    resolved_at        TIMESTAMP NOT NULL,
    realised_value     DOUBLE NOT NULL,
    realised_class     VARCHAR,
    truth_data_hash    VARCHAR NOT NULL,
    PRIMARY KEY (claim_id, resolution_version)
);
"""

TIME_FIELDS = ("as_of", "data_cutoff", "recorded_at", "target_start", "target_end")


def _to_db(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _from_db(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


class Ledger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA)

    def close(self) -> None:
        self.con.close()

    def add_claim(self, claim: Claim) -> None:
        row = claim.model_dump()
        for f in TIME_FIELDS:
            row[f] = _to_db(row[f])
        row["context"] = json.dumps(row["context"], sort_keys=True)
        cols = ",".join(row)
        marks = ",".join("?" for _ in row)
        try:
            self.con.execute(f"INSERT INTO claims ({cols}) VALUES ({marks})", list(row.values()))
        except duckdb.ConstraintException as e:
            raise ValueError(
                f"claim {claim.claim_id} already exists; claims are written once"
            ) from e

    def get_claim(self, claim_id: str) -> Claim:
        cur = self.con.execute("SELECT * FROM claims WHERE claim_id = ?", [claim_id])
        names = [d[0] for d in cur.description]
        found = cur.fetchone()
        if found is None:
            raise KeyError(f"no claim {claim_id}")
        row = dict(zip(names, found, strict=True))
        for f in TIME_FIELDS:
            row[f] = _from_db(row[f])
        row["context"] = json.loads(row["context"])
        return Claim(**row)

    def latest_resolution(self, claim_id: str) -> Resolution | None:
        cur = self.con.execute(
            "SELECT * FROM resolutions WHERE claim_id = ? ORDER BY resolution_version DESC LIMIT 1",
            [claim_id],
        )
        names = [d[0] for d in cur.description]
        found = cur.fetchone()
        if found is None:
            return None
        row = dict(zip(names, found, strict=True))
        row["resolved_at"] = _from_db(row["resolved_at"])
        return Resolution(**row)

    def resolve(
        self,
        claim_id: str,
        realised_value: float,
        truth_data_hash: str,
        realised_class: str | None = None,
    ) -> Resolution:
        """Record what happened. Same data again: no new row. Corrected data: next version."""
        claim = self.get_claim(claim_id)
        latest = self.latest_resolution(claim_id)
        if latest is not None and latest.truth_data_hash == truth_data_hash:
            if latest.realised_value != realised_value:
                raise ValueError(
                    "same truth data gave a different value: resolution not repeatable"
                )
            return latest
        res = Resolution(
            claim_id=claim_id,
            resolution_version=1 if latest is None else latest.resolution_version + 1,
            realised_value=realised_value,
            realised_class=realised_class,
            truth_data_hash=truth_data_hash,
        )
        check_pair(claim, res)
        row = res.model_dump()
        row["resolved_at"] = _to_db(row["resolved_at"])
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.con.execute(f"INSERT INTO resolutions ({cols}) VALUES ({marks})", list(row.values()))
        return res

    def count_resolutions(self, claim_id: str) -> int:
        q = "SELECT count(*) FROM resolutions WHERE claim_id = ?"
        return self.con.execute(q, [claim_id]).fetchone()[0]
