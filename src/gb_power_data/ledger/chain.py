"""Hash chain: makes any later change to a claim or a resolution visible.

Every row written to the ledger also gets one entry in the `chain` table:

    payload_hash = SHA-256 of the row exactly as stored
    entry_hash   = SHA-256 of (previous entry_hash, kind, key, payload_hash)

Each entry depends on all the entries before it. Change an old claim (say, widen its range
after seeing the result) and its payload hash no longer matches; rewrite the chain to hide it
and the last entry_hash (the "head") changes. So the head is published outside the ledger (in
git, in the write-up): anyone holding it can check that nothing before it was changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

GENESIS = "0" * 64  # the "previous hash" of the first entry

SCHEMA = """
CREATE TABLE IF NOT EXISTS chain (
    seq          INTEGER PRIMARY KEY,
    kind         VARCHAR NOT NULL,  -- claim | resolution
    record_key   VARCHAR NOT NULL,  -- claim_id, or claim_id#version for a resolution
    payload_hash VARCHAR NOT NULL,
    prev_hash    VARCHAR NOT NULL,
    entry_hash   VARCHAR NOT NULL,
    chained_at   TIMESTAMP NOT NULL,
    backfilled   BOOLEAN NOT NULL   -- true: row existed before the chain did
);
"""


def _plain(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot hash {type(value).__name__}")


def payload_hash(row: dict) -> str:
    """Hash of a row as stored: same columns and values give the same hash, on any machine."""
    text = json.dumps(row, sort_keys=True, default=_plain, allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def entry_hash(prev: str, kind: str, key: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{kind}|{key}|{payload}".encode()).hexdigest()


def resolution_key(claim_id: str, version: int) -> str:
    return f"{claim_id}#{version}"


@dataclass
class ChainReport:
    entries: int
    backfilled: int
    head: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems