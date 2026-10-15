"""Tests for EventKlineAttributionTool.

Covers:
  - Empty / missing DB returns informative message
  - Single SECURITY event + mock klines → correct forward return
  - Multi-event grouping by (event_type, alert_level)
  - klines insufficient / max-gap guard returns None without crashing
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "agent-runtime"))

from selene_space_agent.tools.event_attribution import EventKlineAttributionTool  # noqa: E402


_NOW_MS = int(time.time() * 1000)
# Event 5 days ago, klines spanning 30 days ago → now
_EVENT_TS_MS = _NOW_MS - 5 * 86_400_000


def _make_macro_db(path: Path, events: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE tg_events (
            timestamp INTEGER,
            event_type TEXT,
            alert_level TEXT,
            magnitude REAL,
            urgency REAL,
            affected TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO tg_events (timestamp, event_type, alert_level, magnitude, urgency, affected) "
        "VALUES (:timestamp, :event_type, :alert_level, :magnitude, :urgency, :affected)",
        events,
    )
    conn.commit()
    conn.close()


def _touch_klines_db(path: Path) -> None:
    """Create empty file so Path.exists() passes; KlineDB is mocked for get_klines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _make_klines_df(timestamps: list[int], closes: list[float]):
    import pandas as pd
    return pd.DataFrame({"timestamp": timestamps, "close": closes})


# ---------- Test 1: missing macro DB returns informative message ----------

def test_missing_macro_db_returns_message():
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = EventKlineAttributionTool(
            macro_db_path=Path(tmpdir) / "missing.db",
            klines_db_path=Path(tmpdir) / "missing_klines.db",
        )
        result = tool.execute({"symbol": "BTCUSDT"})
        assert "macro_events.db not found" in result.output


# ---------- Test 2: empty macro DB returns "No events" ----------

def test_empty_macro_db_returns_no_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        macro_db = Path(tmpdir) / "macro.db"
        _make_macro_db(macro_db, [])
        klines_db = Path(tmpdir) / "klines.db"
        _touch_klines_db(klines_db)
        tool = EventKlineAttributionTool(
            macro_db_path=macro_db,
            klines_db_path=klines_db,
        )
        result = tool.execute({"symbol": "BTCUSDT", "days": 30})
        assert "No events in last 30d" in result.output


# ---------- Test 3: single SECURITY event + mock klines → correct forward return ----------

def test_single_event_forward_return():
    """Event 5d ago at T=100h relative to a kline series starting 10d ago.
    Entry close=100, T+1h close=101 → +1.0% return."""
    with tempfile.TemporaryDirectory() as tmpdir:
        macro_db = Path(tmpdir) / "macro.db"
        _make_macro_db(macro_db, [{
            "timestamp": _EVENT_TS_MS,
            "event_type": "SECURITY",
            "alert_level": "RED",
            "magnitude": 0.8,
            "urgency": 0.7,
            "affected": "BTC",
        }])

        # Klines: 10 days ago → now, hourly. Event is 5d ago (within series).
        # Use a relative anchor: series_start + 100h = _EVENT_TS_MS
        series_start_ms = _EVENT_TS_MS - 100 * 3_600_000  # event at h=100 in series
        kline_ts = [series_start_ms + h * 3_600_000 for h in range(300)]
        kline_close = [100.0 + h for h in range(300)]  # close[100]=200, close[101]=201
        # Forward return from h=100 to h=101: (201-200)/200 = 0.005 = +0.50%
        klines_db = Path(tmpdir) / "klines.db"
        _touch_klines_db(klines_db)

        tool = EventKlineAttributionTool(macro_db_path=macro_db, klines_db_path=klines_db)
        import selene_space_market.kline_db as kline_db_mod
        mock_kdb_cls = MagicMock()
        mock_kdb_cls.return_value.get_klines.return_value = _make_klines_df(kline_ts, kline_close)
        orig = kline_db_mod.KlineDB
        kline_db_mod.KlineDB = mock_kdb_cls
        try:
            result = tool.execute({"symbol": "BTCUSDT", "days": 30, "horizons": [1]})
        finally:
            kline_db_mod.KlineDB = orig

        assert "SECURITY RED" in result.output
        # T+1h: entry=200, exit=201 → return = 0.005 = +0.50%
        assert "T+1h: n=1 avg=+0.0050" in result.output


# ---------- Test 4: multi-event grouping by (event_type, alert_level) ----------

def test_multi_event_grouping():
    """2 SECURITY RED + 1 REGULATORY YELLOW events should produce 2 buckets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        macro_db = Path(tmpdir) / "macro.db"
        _make_macro_db(macro_db, [
            {"timestamp": _EVENT_TS_MS, "event_type": "SECURITY", "alert_level": "RED",
             "magnitude": 0.8, "urgency": 0.7, "affected": "BTC"},
            {"timestamp": _EVENT_TS_MS + 10 * 3_600_000, "event_type": "SECURITY", "alert_level": "RED",
             "magnitude": 0.7, "urgency": 0.6, "affected": "BTC"},
            {"timestamp": _EVENT_TS_MS + 20 * 3_600_000, "event_type": "REGULATORY", "alert_level": "YELLOW",
             "magnitude": 0.5, "urgency": 0.4, "affected": "BTC"},
        ])

        series_start_ms = _EVENT_TS_MS - 100 * 3_600_000
        kline_ts = [series_start_ms + h * 3_600_000 for h in range(300)]
        kline_close = [100.0 + h for h in range(300)]
        klines_db = Path(tmpdir) / "klines.db"
        _touch_klines_db(klines_db)

        tool = EventKlineAttributionTool(macro_db_path=macro_db, klines_db_path=klines_db)
        import selene_space_market.kline_db as kline_db_mod
        mock_kdb_cls = MagicMock()
        mock_kdb_cls.return_value.get_klines.return_value = _make_klines_df(kline_ts, kline_close)
        orig = kline_db_mod.KlineDB
        kline_db_mod.KlineDB = mock_kdb_cls
        try:
            result = tool.execute({"symbol": "BTCUSDT", "days": 30, "horizons": [1]})
        finally:
            kline_db_mod.KlineDB = orig

        assert "[SECURITY RED] n=2" in result.output
        assert "[REGULATORY YELLOW] n=1" in result.output


# ---------- Test 5: max-gap guard returns None when kline gap > 2× horizon ----------

def test_max_gap_guard_rejects_sparse_klines():
    """Event 5d ago, but klines only at -10d and +5d (gap too large for T+1h)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        macro_db = Path(tmpdir) / "macro.db"
        _make_macro_db(macro_db, [{
            "timestamp": _EVENT_TS_MS,
            "event_type": "SECURITY",
            "alert_level": "RED",
            "magnitude": 0.8,
            "urgency": 0.7,
            "affected": "BTC",
        }])

        # Sparse klines: one 10d before event, one 5d after event
        # Gap from event to next kline = 5d = 120h, far exceeds 2×1h
        kline_ts = [_EVENT_TS_MS - 10 * 86_400_000, _EVENT_TS_MS + 5 * 86_400_000]
        kline_close = [100.0, 200.0]
        klines_db = Path(tmpdir) / "klines.db"
        _touch_klines_db(klines_db)

        tool = EventKlineAttributionTool(macro_db_path=macro_db, klines_db_path=klines_db)
        import selene_space_market.kline_db as kline_db_mod
        mock_kdb_cls = MagicMock()
        mock_kdb_cls.return_value.get_klines.return_value = _make_klines_df(kline_ts, kline_close)
        orig = kline_db_mod.KlineDB
        kline_db_mod.KlineDB = mock_kdb_cls
        try:
            result = tool.execute({"symbol": "BTCUSDT", "days": 30, "horizons": [1, 4]})
        finally:
            kline_db_mod.KlineDB = orig

        assert "[SECURITY RED] n=0" in result.output
        assert "T+1h: n=0" in result.output
