"""Hash-chained audit log for exchange operations."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class AuditEntry:
    """A single entry in the audit log, chained to the previous entry via hash."""

    timestamp_ms: int
    action: str
    symbol: str
    details: dict = field(default_factory=dict)
    prev_hash: str = ""
    entry_hash: str = ""


def _compute_hash(prev_hash: str, timestamp_ms: int, action: str, symbol: str, details: dict) -> str:
    """Compute SHA-256 hash for an audit entry."""
    payload = prev_hash + str(timestamp_ms) + action + symbol + json.dumps(details, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    """Immutable hash-chained audit log.

    Each entry's hash depends on the previous entry's hash, forming a chain
    that makes tampering detectable.
    """

    _GENESIS_HASH = "0" * 64

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def append(self, action: str, symbol: str, details: dict | None = None) -> AuditEntry:
        """Append a new entry to the audit log.

        Args:
            action: The action being audited (e.g. "order.place").
            symbol: The trading symbol involved (e.g. "BTC/USDT").
            details: Optional additional details about the action.

        Returns:
            The newly created AuditEntry.
        """
        if details is None:
            details = {}

        prev_hash = self._entries[-1].entry_hash if self._entries else self._GENESIS_HASH
        timestamp_ms = int(time.time() * 1000)
        entry_hash = _compute_hash(prev_hash, timestamp_ms, action, symbol, details)

        entry = AuditEntry(
            timestamp_ms=timestamp_ms,
            action=action,
            symbol=symbol,
            details=details,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        return entry

    def verify(self) -> bool:
        """Verify the entire hash chain is intact.

        Returns:
            True if every entry's prev_hash matches the previous entry's hash
            and every entry's hash is correctly computed.
        """
        expected_prev = self._GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != expected_prev:
                return False
            computed = _compute_hash(entry.prev_hash, entry.timestamp_ms, entry.action, entry.symbol, entry.details)
            if computed != entry.entry_hash:
                return False
            expected_prev = entry.entry_hash
        return True

    def get_entries(self) -> list[AuditEntry]:
        """Return all entries in the log."""
        return list(self._entries)

    def get_last_hash(self) -> str:
        """Return the hash of the most recent entry, or the genesis hash if empty."""
        if not self._entries:
            return self._GENESIS_HASH
        return self._entries[-1].entry_hash

    def to_json(self) -> str:
        """Serialize the entire log to JSON."""
        return json.dumps([asdict(e) for e in self._entries], separators=(",", ":"))

    @classmethod
    def from_json(cls, data: str) -> AuditLog:
        """Deserialize a log from JSON. Verifies the chain after loading.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A verified AuditLog instance.

        Raises:
            ValueError: If the deserialized chain fails verification.
        """
        raw_entries = json.loads(data)
        log = cls()
        for raw in raw_entries:
            entry = AuditEntry(
                timestamp_ms=raw["timestamp_ms"],
                action=raw["action"],
                symbol=raw["symbol"],
                details=raw.get("details", {}),
                prev_hash=raw["prev_hash"],
                entry_hash=raw["entry_hash"],
            )
            log._entries.append(entry)
        if not log.verify():
            raise ValueError("Audit log verification failed: chain integrity check did not pass")
        return log
