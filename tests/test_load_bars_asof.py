"""B1 forward-bars reader: load_bars(after_ms=...) tests.

Two invariants:
1. Default path (after_ms=None) is byte-identical in behaviour to the
   historical read: newest N bars, returned oldest-first.
2. Forward path returns the N bars STARTING AT the anchor (ascending), which
   is what the counterfactual scorer and judge_outcome consume — bars
   strictly after the decision. The end-to-end judge_outcome verdict lives
   with its suite: services/local-api/tests/test_pa_outcome.py
   (test_forward_read_feeds_judge_outcome).

The min_bars interplay is pinned too: forward reads over short horizons need
a lowered min_bars (the default 50 would RuntimeError a 21-bar horizon;
pa_outcome.py:243 precedent uses min_bars=1).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from selene_space_decision_workflow.trading_pipeline import load_bars

HOUR = 3_600_000


def _make_db(tmp_path: Path, closes: list[float], start_ts: int = 0) -> Path:
    db = tmp_path / "klines.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE klines (exchange TEXT, market_type TEXT, symbol TEXT,"
        " interval TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL)"
    )
    for i, c in enumerate(closes):
        ts = start_ts + i * HOUR
        con.execute(
            "INSERT INTO klines VALUES ('binance','futures','TEST/USDT','1h',?,?,?,?,?,0.0)",
            (ts, c, c, c, c),
        )
    con.commit()
    con.close()
    return db


def _record(decided_ts: int) -> dict:
    """A minimal order-carrying record whose decision time is decided_ts."""
    return {
        "meta": {"timestamp": "1970-01-01T00:00:00+00:00"},
        "input_data": {"summary": {"last_close": 100.0}},
        "stage1": {"diagnosis_or_decision": {"gate_result": "proceed"}},
        "stage2": {
            "diagnosis_or_decision": {
                "decision": "trade",
                "order": {
                    "entry": 100.0,
                    "stop_loss": 98.0,
                    "take_profit": 104.0,
                    "direction": "long",
                },
            }
        },
    }


def test_default_path_returns_newest_n_oldest_first(tmp_path):
    closes = [float(i) for i in range(60)]  # 0..59, newest = 59
    db = _make_db(tmp_path, closes)
    bars = load_bars(db, "TEST/USDT", "1h", 10, min_bars=10)
    assert [b["ts"] // HOUR for b in bars] == list(range(50, 60))  # oldest-first
    assert bars[0]["close"] == 50.0 and bars[-1]["close"] == 59.0


def test_forward_path_returns_bars_starting_at_anchor(tmp_path):
    closes = [float(i) for i in range(60)]
    db = _make_db(tmp_path, closes)
    anchor = 50 * HOUR  # bar 50
    bars = load_bars(db, "TEST/USDT", "1h", 5, min_bars=5, after_ms=anchor)
    assert [b["ts"] // HOUR for b in bars] == [50, 51, 52, 53, 54]
    assert bars[0]["close"] == 50.0


def test_forward_path_includes_anchor_bar_itself(tmp_path):
    """timestamp >= anchor: the anchor bar is included (decision on its close)."""
    closes = [float(i) for i in range(10)]
    db = _make_db(tmp_path, closes)
    bars = load_bars(db, "TEST/USDT", "1h", 100, min_bars=0, after_ms=5 * HOUR)
    assert [b["ts"] // HOUR for b in bars] == [5, 6, 7, 8, 9]


def test_forward_path_min_bars_still_enforces(tmp_path):
    db = _make_db(tmp_path, [1.0] * 3)
    with pytest.raises(RuntimeError):
        load_bars(db, "TEST/USDT", "1h", 10, after_ms=0, min_bars=50)
    assert len(load_bars(db, "TEST/USDT", "1h", 10, after_ms=0, min_bars=1)) == 3
