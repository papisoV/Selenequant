"""Tests for scripts/factor_backtest.py.

Covers:
  - ts_to_ms parses ISO8601 text from tg_factors
  - forward_return correct + max-gap guard
  - sample_baseline_timestamps excludes windows around events
  - stats computes mean/median/std
  - empty DB returns error
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]  # packages/tg-ingest/tests/ → repo root
SCRIPTS_DIR = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import factor_backtest as fb  # noqa: E402


# ---------- Test 1: ts_to_ms parses ISO8601 text ----------

def test_ts_to_ms_parses_iso8601_with_microseconds():
    ms = fb.ts_to_ms("2026-07-17 20:20:59.000000")
    assert isinstance(ms, int)
    assert ms > 0


def test_ts_to_ms_parses_iso8601_without_microseconds():
    ms = fb.ts_to_ms("2026-07-17 20:20:59")
    assert isinstance(ms, int)
    assert ms > 0


# ---------- Test 2: forward_return correct + max-gap guard ----------

def test_forward_return_correct():
    """entry close=200 at h=100, exit close=201 at h=101 → return = 0.005."""
    base = 1_000_000
    klines = [(base + h * 3_600_000, 100.0 + h) for h in range(200)]
    r = fb.forward_return(klines, base + 100 * 3_600_000, 1)
    assert r is not None
    assert abs(r - 0.005) < 1e-6


def test_forward_return_max_gap_guard():
    """Gap > 2× horizon → reject (return None)."""
    base = 1_000_000
    # Klines only at h=50 and h=200; event at h=100; horizon=1h, gap=100h >> 2h
    klines = [(base + 50 * 3_600_000, 50.0), (base + 200 * 3_600_000, 200.0)]
    r = fb.forward_return(klines, base + 100 * 3_600_000, 1)
    assert r is None


def test_forward_return_insufficient_klines():
    """Event beyond last kline → None."""
    base = 1_000_000
    klines = [(base + h * 3_600_000, 100.0) for h in range(50)]
    r = fb.forward_return(klines, base + 100 * 3_600_000, 1)
    assert r is None


# ---------- Test 3: sample_baseline_timestamps excludes event windows ----------

def test_sample_baseline_excludes_event_windows():
    base = 1_000_000
    kline_ts = [base + h * 3_600_000 for h in range(500)]
    event_ts = [base + 100 * 3_600_000]  # event at h=100
    out = fb.sample_baseline_timestamps(kline_ts, event_ts, n=50, seed=42, horizon_h_max=24)
    # None of the baseline timestamps should be within 24h of the event
    for ts in out:
        assert abs(ts - event_ts[0]) >= 24 * 3_600_000


def test_sample_baseline_seed_reproducible():
    base = 1_000_000
    kline_ts = [base + h * 3_600_000 for h in range(500)]
    event_ts: list[int] = []
    out1 = fb.sample_baseline_timestamps(kline_ts, event_ts, n=50, seed=42, horizon_h_max=24)
    out2 = fb.sample_baseline_timestamps(kline_ts, event_ts, n=50, seed=42, horizon_h_max=24)
    assert out1 == out2


# ---------- Test 4: stats computes mean/median/std ----------

def test_stats_basic():
    s = fb.stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["n"] == 5
    assert s["mean"] == 3.0
    assert s["median"] == 3.0
    assert s["min"] == 1.0
    assert s["max"] == 5.0
    assert s["std"] > 0


def test_stats_empty():
    s = fb.stats([])
    assert s["n"] == 0
    assert s["mean"] is None


# ---------- Test 5: load_factors filters by event_type + magnitude ----------

def test_load_factors_filters(tmp_path, monkeypatch):
    """Insert 3 factors (2 SECURITY mag>=0.7, 1 macro), verify filter."""
    import sqlite3
    from datetime import datetime, timedelta

    db = tmp_path / "factors.db"
    # load_factors windows on the wall clock ("last N days"), so the fixtures
    # must be relative to now — hardcoded dates silently age out of the window
    # and the assertion fails a couple of months later.
    base = datetime.now().replace(microsecond=0)
    stamp = lambda days_ago: (base - timedelta(days=days_ago)).strftime(  # noqa: E731
        "%Y-%m-%d %H:%M:%S.000000"
    )
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE tg_factors (
            id INTEGER PRIMARY KEY,
            msg_id INTEGER,
            ts TEXT,
            symbols TEXT,
            direction REAL,
            magnitude REAL,
            urgency REAL,
            confidence REAL,
            halflife_min INTEGER,
            event_type TEXT,
            reasoning TEXT,
            created_at TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO tg_factors (msg_id, ts, symbols, direction, magnitude, urgency, confidence, halflife_min, event_type, reasoning, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, stamp(3), "BTC", -0.8, 0.9, 0.7, 0.8, 60, "SECURITY", "test", "2026-07-25"),
            (2, stamp(2), "BTC", -0.7, 0.75, 0.6, 0.7, 60, "SECURITY", "test", "2026-07-26"),
            (3, stamp(1), "BTC", 0.1, 0.3, 0.1, 0.3, 60, "macro", "test", "2026-07-27"),
        ],
    )
    conn.commit()
    conn.close()

    factors = fb.load_factors(str(db), "SECURITY", 0.7, days=60)
    assert len(factors) == 2
    for f in factors:
        assert f["event_type"] == "SECURITY"
        assert f["magnitude"] >= 0.7


# ---------- Test 6: run_backtest returns error on missing DB ----------

def test_run_backtest_missing_factors_db(tmp_path):
    import argparse
    args = argparse.Namespace(
        symbol="BTCUSDT", exchange="binance", market_type="spot", interval="1h",
        event_type="SECURITY", min_magnitude=0.7, days=60, horizons=[1, 4, 24],
        baseline_samples=200, seed=42,
        factors_db=str(tmp_path / "missing.db"),
        klines_db=str(tmp_path / "missing_klines.db"),
        output_dir=str(tmp_path / "out"),
    )
    result = fb.run_backtest(args)
    assert "error" in result
    assert "No factors match" in result["error"]
