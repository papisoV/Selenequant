# Pattern absorbed from US_Stock_Crash_Monitor (MIT (c) 2025 竹林寺/middletoo)
# — 5y rolling percentile credit scoring. Rewrite, no code copied.
# Upstream commit: a7e5003cbd6521c2d5e4b5f9d972aab404d45e57
"""Credit regime source — HY OAS 5y rolling percentile → EventGuard alerts.

Reads the credit_daily(ts, value) table (FRED BAMLH0A0HYM2, written by
scripts/macro_credit_ingest.py) from the canonical macro_events.db.
ts unit: ms epoch (same convention as vix_daily / macro_events.timestamp_utc).

Scoring: percentile of the latest HY OAS value within its trailing 5y window.
  pct >= 0.90 → YELLOW 0.85
  pct >= 0.97 → ORANGE 0.7
  no RED (uncalibrated — vix_source V10 lesson: uncalibrated de-risk costs PnL)
10Y-2Y (yield_daily.t10y2y) is surfaced in get_status() for display only,
zero alerts (inversion != current-period de-risk, no backtest evidence).

Missing DB / missing table / empty table / stale rows → check() returns [],
multiplier stays 1.0, get_status().degraded_reason non-empty (warn-once).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from bisect import bisect_left, bisect_right
from pathlib import Path

from .models import AlertLevel, EventAlert, EventSource

logger = logging.getLogger(__name__)

CREDIT_YELLOW_PCT = 0.90
CREDIT_ORANGE_PCT = 0.97
CREDIT_YELLOW_MULT = 0.85
CREDIT_ORANGE_MULT = 0.70

# A credit alert stands as long as its underlying row is considered fresh.
_FRESH_MS = 7 * 86_400_000
_FIVE_YEAR_MS = int(5 * 365.25 * 86_400_000)
# Below ~1y of daily observations a trailing percentile is noise; stay silent.
_MIN_WINDOW_ROWS = 250

_REPO_MARKER = Path("services") / "local-api" / "app" / "main.py"
_CANONICAL_DB_REL = Path("services") / "local-api" / "data" / "macro_events.db"


def resolve_credit_db_path(credit_db_path: str | Path | None = None) -> Path:
    """Explicit arg > SELENE_MACRO_DB env > repo-resolved canonical path.

    Canonical default mirrors services/local-api/app/main.py:1384-1387
    (env-first, then absolute <repo>/services/local-api/data/macro_events.db)
    but resolves from this file's location, never CWD — the CWD-relative
    "data/macro_events.db" default still present in vix_source/guard is a
    known path-drift landmine (US-007 unified the canonical location).
    """
    if credit_db_path is not None:
        return Path(credit_db_path)
    env = os.environ.get("SELENE_MACRO_DB")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if (parent / _REPO_MARKER).exists():
            return parent / _CANONICAL_DB_REL
    # Outside a repo checkout there is no trustworthy absolute target; an
    # explicit CWD-derived absolute path degrades honestly via get_status().
    return (Path.cwd() / _CANONICAL_DB_REL).resolve()


class CreditSource:
    """Generates credit-regime alerts from macro_events.db credit_daily table."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._cache_ts: int = 0
        self._data: list[tuple[int, float]] = []  # (ts_ms, hy_oas) ts-ascending
        self._ts: list[int] = []
        self._cache_ttl_ms = 3600_000
        self._warned = False
        self._degraded_reason: str | None = None
        self._partial_window = False
        self._window_rows = 0
        self._yield_t10y2y: float | None = None

    def _degrade(self, reason: str) -> None:
        self._degraded_reason = reason
        if not self._warned:
            logger.warning("CreditSource degraded: %s", reason)
            self._warned = True

    def _ensure_cache(self, now_ts: int) -> None:
        """Refresh credit cache from DB if stale (VixSource pattern, TTL 1h)."""
        if now_ts - self._cache_ts < self._cache_ttl_ms and self._data:
            return
        if not self._db_path.exists():
            self._degrade(f"macro_events.db not found at {self._db_path}")
            return
        try:
            conn = sqlite3.connect(str(self._db_path))
        except (sqlite3.Error, ValueError, TypeError, OverflowError) as e:
            logger.error("CreditSource cannot open %s: %s", self._db_path, e)
            self._degrade(f"cannot open {self._db_path}: {e}")
            return
        try:
            cur = conn.execute("SELECT ts, value FROM credit_daily ORDER BY ts ASC")
            self._data = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
            self._ts = [d[0] for d in self._data]
            self._yield_t10y2y = None
            try:
                yrow = conn.execute(
                    "SELECT ts, t10y2y FROM yield_daily ORDER BY ts DESC LIMIT 1"
                ).fetchone()
                if yrow is not None and yrow[1] is not None:
                    self._yield_t10y2y = float(yrow[1])
            except sqlite3.OperationalError:
                pass  # yield_daily is display-only; absence is not a degrade
        except (sqlite3.Error, ValueError, TypeError, OverflowError) as e:
            self._data = []
            self._ts = []
            self._degrade(
                f"credit_daily table missing/unreadable in {self._db_path}: {e}"
            )
            return
        finally:
            conn.close()
        self._cache_ts = now_ts
        if self._data:
            self._degraded_reason = None
            logger.debug("CreditSource cache refreshed: %d rows", len(self._data))
        else:
            self._degrade(f"credit_daily table empty in {self._db_path}")

    def _latest_index(self, now_ts: int) -> int | None:
        """Index of the most recent row with ts <= now_ts, or None."""
        if not self._ts:
            return None
        idx = bisect_right(self._ts, now_ts) - 1
        return idx if idx >= 0 else None

    def _percentile(self, row_ts: int, value: float) -> tuple[float, int]:
        """Percentile of `value` within the trailing 5y window ending at row_ts."""
        start = bisect_left(self._ts, row_ts - _FIVE_YEAR_MS)
        end = bisect_right(self._ts, row_ts)
        window = sorted(v for _, v in self._data[start:end])
        n = len(window)
        self._window_rows = n
        self._partial_window = n > 0 and (row_ts - self._data[start][0]) < _FIVE_YEAR_MS
        if n == 0:
            return 0.0, 0
        return bisect_right(window, value) / n, n

    def check(self, symbol: str, now_ts: int) -> list[EventAlert]:
        """Return credit-regime alerts (symbol="*", event_type="CREDIT")."""
        self._ensure_cache(now_ts)
        idx = self._latest_index(now_ts)
        if idx is None:
            return []
        row_ts, value = self._data[idx]
        if now_ts - row_ts >= _FRESH_MS:
            self._degrade(
                f"credit_daily stale: last row {now_ts - row_ts}ms old at {self._db_path}"
            )
            return []
        pct, n = self._percentile(row_ts, value)
        if n < _MIN_WINDOW_ROWS:
            self._degrade(
                f"credit_daily insufficient history: {n} rows (< {_MIN_WINDOW_ROWS})"
            )
            return []
        if pct >= CREDIT_ORANGE_PCT:
            level, mult = AlertLevel.ORANGE, CREDIT_ORANGE_MULT
        elif pct >= CREDIT_YELLOW_PCT:
            level, mult = AlertLevel.YELLOW, CREDIT_YELLOW_MULT
        else:
            return []
        return [
            EventAlert(
                source=EventSource.CREDIT,
                level=level,
                symbol="*",
                reason=f"HY OAS {value:.2f} at {pct:.1%} of trailing credit window ({n} rows)",
                start_ts=row_ts,
                end_ts=row_ts + _FRESH_MS,
                multiplier=mult,
                event_type="CREDIT",
            )
        ]

    def get_status(self, now_ts: int | None = None) -> dict:
        """Observability: {available, last_row_ts, degraded_reason, ...}."""
        if now_ts is None:
            now_ts = int(time.time() * 1000)
        self._ensure_cache(now_ts)
        last_row_ts: int | None = None
        stale = False
        idx = self._latest_index(now_ts)
        if idx is not None:
            last_row_ts = self._data[idx][0]
            stale = (now_ts - last_row_ts) >= _FRESH_MS
            if not stale:
                self._percentile(last_row_ts, self._data[idx][1])
        available = bool(self._data) and last_row_ts is not None and not stale
        reason = self._degraded_reason
        if not available and reason is None:
            if last_row_ts is None and self._data:
                reason = "credit_daily has no row at or before now_ts"
            elif last_row_ts is not None and stale:
                reason = f"credit_daily stale: last row {now_ts - last_row_ts}ms old"
        return {
            "available": available,
            "last_row_ts": last_row_ts,
            "degraded_reason": reason,
            "partial_window": self._partial_window,
            "window_rows": self._window_rows,
            "yield_t10y2y": self._yield_t10y2y,
        }
