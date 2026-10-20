"""Append-only claim ledger on duckdb
Guarantee at this stage:
-rows are only ever added: the class has no update or delete method
-a claim _id can be written once;
-resolving twice from the same data returns the existing row instead
-resolving from corrected data adds the next version and keeps the
-every row also gets a hash-chain entry in the same transaction (see chain.py),
 so a later change to any row is detected by verify()
all times are stored in UTC
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from gb_power_data.ledger import chain
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
        self.con.execute(chain.SCHEMA)
        self._backfill_once()

    def close(self) -> None:
        self.con.close()

    def add_claim(self, claim: Claim) -> None:
        row = claim.model_dump()
        for f in TIME_FIELDS:
            row[f] = _to_db(row[f])
        row["context"] = json.dumps(row["context"], sort_keys=True)
        cols = ",".join(row)
        marks = ",".join("?" for _ in row)
        self.con.begin()  # the row and its chain entry are written together, or not at all
        try:
            self.con.execute(f"INSERT INTO claims ({cols}) VALUES ({marks})", list(row.values()))
            self._chain("claim", claim.claim_id, row)
        except duckdb.ConstraintException as e:
            self.con.rollback()
            raise ValueError(
                f"claim {claim.claim_id} already exists; claims are written once"
            ) from e
        except Exception:
            self.con.rollback()
            raise
        self.con.commit()

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
        self.con.begin()
        try:
            self.con.execute(
                f"INSERT INTO resolutions ({cols}) VALUES ({marks})", list(row.values())
            )
            key = chain.resolution_key(claim_id, res.resolution_version)
            self._chain("resolution", key, row)
        except Exception:
            self.con.rollback()
            raise
        self.con.commit()
        return res

    def count_resolutions(self, claim_id: str) -> int:
        q = "SELECT count(*) FROM resolutions WHERE claim_id = ?"
        return self.con.execute(q, [claim_id]).fetchone()[0]

    # ---- hash chain -------------------------------------------------------------------

    def head(self) -> str:
        """entry_hash of the newest chain entry: publish it to anchor the ledger."""
        found = self.con.execute(
            "SELECT entry_hash FROM chain ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return chain.GENESIS if found is None else found[0]

    def _chain(self, kind: str, key: str, row: dict, backfilled: bool = False) -> None:
        last = self.con.execute(
            "SELECT seq, entry_hash FROM chain ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, prev = (0, chain.GENESIS) if last is None else (last[0] + 1, last[1])
        payload = chain.payload_hash(row)
        self.con.execute(
            "INSERT INTO chain VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                seq,
                kind,
                key,
                payload,
                prev,
                chain.entry_hash(prev, kind, key, payload),
                _to_db(datetime.now(UTC)),
                backfilled,
            ],
        )

    def _rows(self, table: str, order: str) -> list[dict]:
        cur = self.con.execute(f"SELECT * FROM {table} ORDER BY {order}")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def _backfill_once(self) -> None:
        """A ledger made before the chain existed: chain its rows once, marked backfilled.
        Only when the chain is empty: a row added later without an entry must stay visible
        as unchained, not be quietly adopted."""
        if self.con.execute("SELECT count(*) FROM chain").fetchone()[0] > 0:
            return
        claims = self._rows("claims", "recorded_at, claim_id")
        resolutions = self._rows("resolutions", "resolved_at, claim_id, resolution_version")
        if not claims and not resolutions:
            return
        self.con.begin()
        try:
            for row in claims:
                self._chain("claim", row["claim_id"], row, backfilled=True)
            for row in resolutions:
                key = chain.resolution_key(row["claim_id"], row["resolution_version"])
                self._chain("resolution", key, row, backfilled=True)
        except Exception:
            self.con.rollback()
            raise
        self.con.commit()

    def verify(self, expected_head: str | None = None) -> chain.ChainReport:
        """Recompute every hash from the rows as they are now and check every link.

        expected_head: a head published earlier. It must still be in the chain; entries added
        after it are fine (the ledger grows), but nothing up to it may have changed."""
        problems: list[str] = []
        stored = {
            "claim": {r["claim_id"]: r for r in self._rows("claims", "claim_id")},
            "resolution": {
                chain.resolution_key(r["claim_id"], r["resolution_version"]): r
                for r in self._rows("resolutions", "claim_id, resolution_version")
            },
        }
        entries = self._rows("chain", "seq")
        prev, seen = chain.GENESIS, {"claim": set(), "resolution": set()}
        for i, e in enumerate(entries):
            where = f"entry {e['seq']} ({e['kind']} {e['record_key']})"
            if e["seq"] != i:
                problems.append(f"{where}: sequence gap, expected {i}")
            if e["prev_hash"] != prev:
                problems.append(f"{where}: does not link to the entry before it")
            expected = chain.entry_hash(prev, e["kind"], e["record_key"], e["payload_hash"])
            if e["entry_hash"] != expected:
                problems.append(f"{where}: entry hash was changed")
            row = stored[e["kind"]].get(e["record_key"])
            if row is None:
                problems.append(f"{where}: row is missing (deleted)")
            elif chain.payload_hash(row) != e["payload_hash"]:
                problems.append(f"{where}: row was changed after it was written")
            seen[e["kind"]].add(e["record_key"])
            prev = e["entry_hash"]
        for kind, rows in stored.items():
            for key in sorted(set(rows) - seen[kind]):
                problems.append(
                    f"{kind} {key}: row has no chain entry (written outside the ledger)"
                )
        if expected_head is not None and expected_head not in {e["entry_hash"] for e in entries}:
            problems.append("published head not found: the chain before it was rewritten")
        return chain.ChainReport(
            entries=len(entries),
            backfilled=sum(e["backfilled"] for e in entries),
            head=prev,
            problems=problems,
        )
