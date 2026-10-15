# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis (commit ce364e45)
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: stdlib-only, SQLite persistence, SSRF guard.
"""Notification noise control: dedup / cooldown / quiet-hours suppression.

Mirrors the semantics of daily_stock_analysis ``notification_noise.py``
(commit ce364e45): quiet-hours are checked before state lookups, dedup
before cooldown, and a decision to allow records both expiry timestamps.
The two deviations from upstream are deliberate:

- Persistence: DSA keeps process-local dicts; Selene persists to a
  self-managed SQLite file (no migrations_pkg registration), so state
  survives restarts and is shared across workers.
- Write timing: DSA reserves in-flight slots on allow and records expiry
  after a successful send (two-phase). Selene's ``evaluate`` records the
  expiry timestamps directly on allow (single-phase), which is the
  contract the notification layer needs: a suppressed retry is preferred
  over a duplicated send after a crash between reserve and record.

The gate is fail-open: unexpected errors produce an allow decision so a
broken noise store can never silence notifications.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NOISE_DB_ENV_VAR = "SELENE_PULSE_NOISE_DB"
_DEFAULT_NOISE_DB = Path.home() / ".selenespace" / "pulse_noise.db"

_QUIET_HOURS_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Decision:
    """Outcome of a noise-control evaluation."""

    allowed: bool
    reason: str


class NoiseStore:
    """SQLite-backed expiry store for dedup/cooldown keys.

    Single table, self-managed file (see the serenity ``pool_db``
    precedent): deliberately not registered in migrations_pkg.
    Connections are opened per operation so the store is safe to share
    across threads and never holds a stale handle.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path))
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS noise_state ("
                "key TEXT PRIMARY KEY, expires_at REAL NOT NULL)"
            )

    def expires_at(self, key: str, now_ts: float) -> float | None:
        """Return the stored expiry timestamp for *key* if it is still active."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at FROM noise_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        expires_at = float(row[0])
        if expires_at <= now_ts:
            return None
        return expires_at

    def record(self, key: str, expires_at: float) -> None:
        """Write an expiry timestamp, replacing any previous value."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO noise_state (key, expires_at) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET expires_at = excluded.expires_at",
                (key, expires_at),
            )
            connection.commit()

    def purge_expired(self, now_ts: float) -> None:
        """Delete rows whose expiry has passed (housekeeping, best-effort)."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM noise_state WHERE expires_at <= ?", (now_ts,)
            )
            connection.commit()


def resolve_noise_db_path() -> Path:
    """Resolve the noise DB path: ``SELENE_PULSE_NOISE_DB`` env override, else default."""
    env_value = os.getenv(NOISE_DB_ENV_VAR, "").strip()
    if env_value:
        return Path(env_value)
    return _DEFAULT_NOISE_DB


def parse_quiet_hours(value: str | None) -> tuple[int, int] | None:
    """Parse ``HH:MM-HH:MM`` into start/end minute-of-day values (DSA format)."""
    raw = str(value or "").strip()
    if not raw:
        return None
    match = _QUIET_HOURS_RE.match(raw)
    if not match:
        raise ValueError("quiet_hours must be in HH:MM-HH:MM format")
    start_hour, start_minute, end_hour, end_minute = (
        int(group) for group in match.groups()
    )
    return start_hour * 60 + start_minute, end_hour * 60 + end_minute


def is_time_in_quiet_hours(now: datetime, quiet_hours: tuple[int, int]) -> bool:
    """Return whether *now* falls inside a quiet-hours interval.

    Cross-midnight windows (start > end, e.g. 22:00-08:00) wrap around,
    matching DSA ``is_time_in_quiet_hours``. start == end means no quiet
    period at all.
    """
    start_minute, end_minute = quiet_hours
    minute_of_day = now.hour * 60 + now.minute
    if start_minute == end_minute:
        return False
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


class NotificationNoise:
    """Noise-control gate combining a :class:`NoiseStore` with quiet-hours config."""

    def __init__(self, store: NoiseStore, quiet_hours: str | None = None) -> None:
        self._store = store
        self._quiet_hours = parse_quiet_hours(quiet_hours)

    def evaluate(
        self,
        dedup_key: str,
        cooldown_key: str,
        dedup_ttl: int,
        cooldown_ttl: int,
        now: datetime | None = None,
    ) -> Decision:
        """Decide whether a notification should be sent.

        Check order mirrors DSA: quiet-hours first (pure config), then
        dedup, then cooldown. On allow, both expiry timestamps are
        recorded immediately. Fail-open on unexpected errors.
        """
        try:
            return self._evaluate(dedup_key, cooldown_key, dedup_ttl, cooldown_ttl, now)
        except Exception:
            return Decision(allowed=True, reason="noise_check_failed_open")

    def _evaluate(
        self,
        dedup_key: str,
        cooldown_key: str,
        dedup_ttl: int,
        cooldown_ttl: int,
        now: datetime | None,
    ) -> Decision:
        effective_now = (
            now.astimezone()
            if now is not None and now.tzinfo is not None
            else (now if now is not None else datetime.now().astimezone())
        )
        now_ts = effective_now.timestamp()

        if self._quiet_hours and is_time_in_quiet_hours(
            effective_now, self._quiet_hours
        ):
            return Decision(allowed=False, reason="quiet_hours")

        dedup_ttl = max(0, int(dedup_ttl))
        cooldown_ttl = max(0, int(cooldown_ttl))

        if dedup_ttl > 0 and self._store.expires_at(dedup_key, now_ts) is not None:
            return Decision(allowed=False, reason="dedup")
        if (
            cooldown_ttl > 0
            and self._store.expires_at(cooldown_key, now_ts) is not None
        ):
            return Decision(allowed=False, reason="cooldown")

        if dedup_ttl > 0:
            self._store.record(dedup_key, now_ts + dedup_ttl)
        if cooldown_ttl > 0:
            self._store.record(cooldown_key, now_ts + cooldown_ttl)
        return Decision(allowed=True, reason="allowed")
