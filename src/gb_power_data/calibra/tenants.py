"""Customers of a hosted Calibra: one API key and one separate ledger each.

Keys are shown once when created and stored only as SHA-256 hashes, so the server's files never
hold a usable key. Each customer's forecasts live in their own ledger file: no query can mix
two customers' records.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
PREFIX = "cal_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Tenants:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.file = self.root / "tenants.json"
        self._lock = threading.Lock()
        self._ledger_locks: dict[str, threading.Lock] = {}

    def _read(self) -> dict:
        if not self.file.exists():
            return {}
        return json.loads(self.file.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.file)  # atomic: a crash never leaves a half-written file

    def create(self, name: str) -> str:
        """New customer; returns their API key, which is not stored and cannot be shown again."""
        if not NAME.match(name):
            raise ValueError("name: 2-41 lower-case letters, digits or '-', starting with one")
        with self._lock:
            data = self._read()
            if name in data:
                raise ValueError(f"customer {name!r} already exists; use rotate for a new key")
            key = PREFIX + secrets.token_urlsafe(32)
            data[name] = {"key_sha256": _hash(key), "created": datetime.now(UTC).isoformat()}
            self._write(data)
        return key

    def rotate(self, name: str) -> str:
        with self._lock:
            data = self._read()
            if name not in data:
                raise ValueError(f"no customer {name!r}")
            key = PREFIX + secrets.token_urlsafe(32)
            data[name]["key_sha256"] = _hash(key)
            data[name]["rotated"] = datetime.now(UTC).isoformat()
            self._write(data)
        return key

    def who(self, key: str | None) -> str | None:
        """The customer this key belongs to, or None."""
        if not key or not key.startswith(PREFIX):
            return None
        h = _hash(key)
        for name, rec in self._read().items():
            if hmac.compare_digest(rec["key_sha256"], h):  # constant time
                return name
        return None

    def names(self) -> list[str]:
        return sorted(self._read())

    def ledger(self, name: str) -> Path:
        return self.root / "customers" / name / "ledger.duckdb"

    def lock(self, name: str) -> threading.Lock:
        """One writer at a time per customer ledger (duckdb allows a single writer)."""
        with self._lock:
            return self._ledger_locks.setdefault(name, threading.Lock())
