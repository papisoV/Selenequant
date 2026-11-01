"""Smoke test for scripts/tg_ingest_to_events.py.

Verifies:
- ts_str_to_ms converts tg_factors.ts string to ms epoch correctly
- classify_message filters noise correctly
- Full ingest pipeline populates tg_events with expected counts

Uses a temporary macro_events.db so we don't touch the real data.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
sys.path.insert(0, str(REPO / "scripts"))

from tg_ingest_to_events import ts_str_to_ms  # type: ignore


def test_ts_str_to_ms_strips_microseconds():
    ms = ts_str_to_ms("2026-07-17 20:20:59.123456")
    expected = int(datetime(2026, 7, 17, 20, 20, 59).timestamp() * 1000)
    assert ms == expected, f"{ms} != {expected}"


def test_ts_str_to_ms_no_microseconds():
    ms = ts_str_to_ms("2025-11-01 03:38:20")
    expected = int(datetime(2025, 11, 1, 3, 38, 20).timestamp() * 1000)
    assert ms == expected


def test_classify_message_security():
    from selene_space_alpha.event_guard.tg_source import classify_message

    result = classify_message("Balancer exploit drained $635M from contract")
    assert result is not None
    event_type, level, affected, half_life = result
    assert event_type == "SECURITY"
    assert affected == "*"
    assert half_life == 2.0


def test_classify_message_noise_returns_none():
    from selene_space_alpha.event_guard.tg_source import classify_message

    # A neutral price update — not an actionable event
    assert classify_message("BTC price up 2% today") is None
    assert classify_message("ETH trading volume normal") is None


def test_classify_message_regulatory():
    from selene_space_alpha.event_guard.tg_source import classify_message

    result = classify_message("SEC files lawsuit against major exchange for compliance violations")
    assert result is not None
    event_type, *_ = result
    assert event_type == "REGULATORY"


def test_classify_message_whale():
    from selene_space_alpha.event_guard.tg_source import classify_message

    result = classify_message("foundation sell 50K ETH from treasury wallet")
    assert result is not None
    event_type, *_ = result
    assert event_type == "WHALE"


def test_event_guard_get_tg_multiplier(tmp_path):
    """get_tg_multiplier should consult TG source only (no macro, no VIX).

    Regression: tg-spike-v1 must apply TG-only protection (SECURITY/REGULATORY
    ±2h) without double-cutting the macro event multiplier (which TGSpikeSource
    already applies at signal-generation time).
    """
    from selene_space_alpha.event_guard import EventGuard
    from selene_space_alpha.event_guard.tg_source import TGSource

    macro_db = tmp_path / "macro_events.db"
    src = TGSource(db_path=macro_db)

    # SECURITY orange event at ts=1000, half_life=2h
    ts_ms = 1_000_000
    alert = src.ingest_message(ts_ms, "Balancer exploit drained $635M from contract")
    assert alert is not None
    assert alert.event_type == "SECURITY"

    # Wire up EventGuard with TG source enabled
    guard = EventGuard(macro_db_path=macro_db, enable_vix=False, enable_tg=True)

    # +30min inside ±2h window → SECURITY orange mult 0.7
    m_active = guard.get_tg_multiplier("BTC/USDT", ts_ms + 30 * 60 * 1000)
    assert abs(m_active - 0.7) < 0.01, f"expected 0.7, got {m_active}"

    # +3h after event → expired, returns 1.0
    m_expired = guard.get_tg_multiplier("BTC/USDT", ts_ms + 3 * 3_600_000)
    assert m_expired == 1.0, f"expected 1.0, got {m_expired}"

    # 1h before event → no active alert, returns 1.0
    m_before = guard.get_tg_multiplier("BTC/USDT", ts_ms - 3_600_000)
    assert m_before == 1.0, f"expected 1.0, got {m_before}"


def test_get_position_multiplier_no_macro_tg_double_cut(tmp_path):
    """FOMC + TG security co-fire → min() not multiply (US-005).

    Regression: the calendar×TG double-cut is eliminated. At +30min a FOMC is
    in its ±2h RED window (0.7); co-firing TG SECURITY is orange (0.7). min()
    gives 0.7 (single most-restrictive reduction); the old *= rule would give
    0.49 — a cut neither source intended. Catastrophic TG (RED=0.0) still
    blocks during any macro window (0.0 wins via min).
    """
    from selene_space_alpha.event_guard import EventGuard
    from selene_space_alpha.event_guard.tg_source import TGSource, _ensure_schema

    ts_ms = 1_000_000_000

    # --- co-fire db: FOMC macro + TG SECURITY at same ts ---
    macro_db = tmp_path / "macro_events.db"
    conn = sqlite3.connect(str(macro_db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS macro_events "
        "(event_type TEXT, date TEXT, timestamp_utc INTEGER)"
    )
    conn.execute(
        "INSERT INTO macro_events VALUES ('FOMC', '2026-01-01', ?)", (ts_ms,)
    )
    conn.commit()
    conn.close()

    src = TGSource(db_path=macro_db)
    alert = src.ingest_message(ts_ms, "Balancer exploit drained $635M from contract")
    assert alert is not None and abs(alert.multiplier - 0.7) < 0.01

    guard = EventGuard(macro_db_path=macro_db, enable_vix=False, enable_tg=True)
    now = ts_ms + 30 * 60 * 1000
    m = guard.get_position_multiplier("BTC/USDT", now)
    assert abs(m - 0.7) < 0.01, f"expected min(0.7 FOMC-RED, 0.7 TG)=0.7, got {m}"  # not 0.49

    # --- single TG (no macro): behavior unchanged, returns 0.7 ---
    tg_only_db = tmp_path / "tg_only.db"
    src2 = TGSource(db_path=tg_only_db)
    src2.ingest_message(ts_ms, "Balancer exploit drained $635M from contract")
    guard2 = EventGuard(macro_db_path=tg_only_db, enable_vix=False, enable_tg=True)
    m2 = guard2.get_position_multiplier("BTC/USDT", now)
    assert abs(m2 - 0.7) < 0.01, f"expected 0.7, got {m2}"

    # --- catastrophic TG RED during macro window: min() keeps the block ---
    # (this is the case the plan's max() got wrong — max(0.7 FOMC, 0.0 TG) = 0.7,
    #  full-size trading during a hack)
    red_db = tmp_path / "red.db"
    conn = sqlite3.connect(str(red_db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS macro_events "
        "(event_type TEXT, date TEXT, timestamp_utc INTEGER)"
    )
    conn.execute(
        "INSERT INTO macro_events VALUES ('FOMC', '2026-01-01', ?)", (ts_ms,)
    )
    _ensure_schema(conn)
    conn.execute(
        """INSERT INTO tg_events
           (timestamp, message, event_type, alert_level, affected, half_life_h)
           VALUES (?,?,?,?,?,?)""",
        (ts_ms, "catastrophic hack", "SECURITY", "red", "*", 2.0),
    )
    conn.commit()
    conn.close()
    guard3 = EventGuard(macro_db_path=red_db, enable_vix=False, enable_tg=True)
    m3 = guard3.get_position_multiplier("BTC/USDT", now)
    assert m3 == 0.0, f"catastrophic RED must block during macro window, got {m3}"


def test_tg_spike_source_tg_overlay_reduces_strength_on_security_event(tmp_path):
    """TGSpikeSource should apply ±2h SECURITY/REGULATORY overlay (×0.7) at
    signal-generation time, mirroring EventGuard.get_tg_multiplier live behavior
    so factory backtests don't diverge from live execution."""
    from selene_space_alpha.factors.tg_spike_source import (
        TGSpikeSource,
        TG_OVERLAY_POSITION_MULT,
    )
    from selene_space_alpha.event_guard.tg_source import TGSource

    # Build a macro_events.db with one SECURITY orange event at ts=1000
    macro_db = tmp_path / "macro_events.db"
    src = TGSource(db_path=macro_db)
    ts_event = 1_000_000
    alert = src.ingest_message(ts_event, "Balancer exploit drained $635M from contract")
    assert alert is not None and alert.event_type == "SECURITY"

    # Build a tiny synthetic klines DataFrame with a spike bar inside the ±2h window.
    # bar at ts=1_100_000 (1000s after event = 16.6 min, inside ±2h)
    import pandas as pd
    bar_ts = pd.Timestamp(ts_event + 100_000, unit="ms", tz="UTC")
    # Need at least SPIKE_MIN_PERIODS=20 bars before spike bar to compute z-score.
    # Build 25 bars, last one is the spike bar with elevated vol_trigger.
    times = pd.date_range(end=bar_ts, periods=25, freq="4h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": [int(t.value // 10**6) for t in times],
        "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0,
    }, index=times)

    # Use the TGSpikeSource with a synthetic tg_factors.db that has a signal
    # in the decay window of the spike bar so vol_trigger > 0.
    tg_db = tmp_path / "tg_factors.db"
    conn = sqlite3.connect(str(tg_db))
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS tg_factors (id INTEGER PRIMARY KEY, ts TEXT, symbols TEXT,
          direction REAL, magnitude REAL, urgency REAL, confidence REAL,
          halflife_min REAL, event_type TEXT, reasoning TEXT);
        INSERT INTO tg_factors (ts, symbols, direction, magnitude, urgency, confidence,
          halflife_min, event_type, reasoning) VALUES
          ('{bar_ts.strftime('%Y-%m-%d %H:%M:%S')}', '["*"]', -0.5, 0.8, 0.8, 0.8,
           360, 'security', 'SEC investigation triggers spike');
        """
    )
    conn.commit()
    conn.close()

    # Build source with TG overlay ON and OFF
    src_on = TGSpikeSource(
        tg_db_path=tg_db, symbol="BTC/USDT", interval="4h",
        macro_db_path=macro_db, enable_macro_overlay=False, enable_tg_overlay=True,
    )
    src_off = TGSpikeSource(
        tg_db_path=tg_db, symbol="BTC/USDT", interval="4h",
        macro_db_path=macro_db, enable_macro_overlay=False, enable_tg_overlay=False,
    )
    # If the spike bar fires a signal, the ON variant should have strength × 0.7.
    # If no signal fires (synthetic data may not exceed z=2.0), still verify
    # _tg_overlay_mult returns the expected value at the event window.
    m_on = src_on._tg_overlay_mult(bar_ts)
    m_off = src_off._tg_overlay_mult(bar_ts)
    assert abs(m_on - TG_OVERLAY_POSITION_MULT) < 0.01, f"expected {TG_OVERLAY_POSITION_MULT}, got {m_on}"
    assert m_off == 1.0, f"overlay disabled should return 1.0, got {m_off}"


def test_ingest_llm_factor_passes_through_llm_event_type(tmp_path):
    """ingest_llm_factor should NOT reclassify via keywords — it should map
    the LLM event_type directly to EventGuard event_type, picking alert level
    from magnitude/urgency.

    Regression: legacy ingest_message() ran keyword classify_message() on
    the reasoning text, which discarded the LLM's event_type/magnitude/urgency.
    """
    from selene_space_alpha.event_guard import EventGuard
    from selene_space_alpha.event_guard.tg_source import TGSource, _ensure_schema

    macro_db = tmp_path / "macro_events.db"
    src = TGSource(db_path=macro_db)
    ts_ms = 1_000_000

    alert = src.ingest_llm_factor(
        timestamp_ms=ts_ms,
        message="SEC announces enforcement action",  # would match 'SEC' keyword anyway
        llm_event_type="regulatory",
        magnitude=0.8,
        urgency=0.7,
        symbols="*",
    )
    assert alert is not None
    assert alert.event_type == "REGULATORY"
    assert alert.level.value == "orange"
    assert alert.multiplier == 0.7  # REGULATORY ORANGE per _TG_EVENT_MULTIPLIERS

    # LLM factor columns persisted in tg_events
    conn = sqlite3.connect(str(macro_db))
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT event_type, alert_level, llm_event_type, magnitude, urgency "
        "FROM tg_events"
    ).fetchone()
    assert row[0] == "REGULATORY"
    assert row[1] == "orange"
    assert row[2] == "regulatory"
    assert abs(row[3] - 0.8) < 0.01
    assert abs(row[4] - 0.7) < 0.01
    conn.close()

    # EventGuard reads it back with the right multiplier
    guard = EventGuard(macro_db_path=macro_db, enable_vix=False, enable_tg=True)
    m_active = guard.get_tg_multiplier("BTC/USDT", ts_ms + 30 * 60 * 1000)
    assert abs(m_active - 0.7) < 0.01, f"expected 0.7, got {m_active}"


def test_ingest_llm_factor_drops_unmapped_event_types(tmp_path):
    """LLM event types not in the actionable mapping (macro/listing/
    partnership) must return None — they are handled elsewhere (macro overlay
    at TGSpikeSource) or had no alpha in research."""
    from selene_space_alpha.event_guard.tg_source import TGSource, _ensure_schema

    macro_db = tmp_path / "macro_events.db"
    src = TGSource(db_path=macro_db)

    for unmapped in ("macro", "listing", "partnership", "other"):
        alert = src.ingest_llm_factor(
            timestamp_ms=1_000_000,
            message="any text",
            llm_event_type=unmapped,
            magnitude=0.9,
            urgency=0.9,
        )
        assert alert is None, f"{unmapped} should be dropped, got {alert}"

    # Verify nothing was written to DB
    conn = sqlite3.connect(str(macro_db))
    _ensure_schema(conn)
    count = conn.execute("SELECT COUNT(*) FROM tg_events").fetchone()[0]
    assert count == 0, f"expected 0 rows, got {count}"
    conn.close()


def test_ingest_llm_factor_market_mapped(tmp_path):
    """market (price-breakout) events now map to EventGuard (2026-08-14 policy).

    Forward-vol research showed market-BTC persists 24-48h (1.13-1.19x, p≤0.004);
    it joined the LIVE EventGuard reduction set alongside security/regulatory.
    """
    from selene_space_alpha.event_guard import EventGuard
    from selene_space_alpha.event_guard.tg_source import TGSource, _ensure_schema

    macro_db = tmp_path / "macro_events.db"
    src = TGSource(db_path=macro_db)
    ts_ms = 1_000_000

    alert = src.ingest_llm_factor(
        timestamp_ms=ts_ms,
        message="BTC breaks key resistance",
        llm_event_type="market",
        magnitude=0.8,
        urgency=0.7,
        symbols="*",
    )
    assert alert is not None
    assert alert.event_type == "MARKET"
    assert alert.level.value == "orange"
    assert alert.multiplier == 0.7  # MARKET ORANGE per _TG_EVENT_MULTIPLIERS

    # LLM factor columns persisted in tg_events
    conn = sqlite3.connect(str(macro_db))
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT event_type, alert_level, llm_event_type, magnitude, urgency "
        "FROM tg_events"
    ).fetchone()
    assert row[0] == "MARKET"
    assert row[1] == "orange"
    assert row[2] == "market"
    assert abs(row[3] - 0.8) < 0.01
    assert abs(row[4] - 0.7) < 0.01
    conn.close()

    # EventGuard reads it back with the right multiplier
    guard = EventGuard(macro_db_path=macro_db, enable_vix=False, enable_tg=True)
    m_active = guard.get_tg_multiplier("BTC/USDT", ts_ms + 30 * 60 * 1000)
    assert abs(m_active - 0.7) < 0.01, f"expected 0.7, got {m_active}"


def test_ingest_llm_factor_magnitude_level_routing(tmp_path):
    """Alert level should follow magnitude/urgency thresholds, not keywords."""
    from selene_space_alpha.event_guard.tg_source import TGSource

    macro_db = tmp_path / "macro_events.db"
    src = TGSource(db_path=macro_db)
    ts_ms = 1_000_000

    # mag=0.8 >= 0.7 → ORANGE
    alert_high = src.ingest_llm_factor(
        timestamp_ms=ts_ms, message="whale move",
        llm_event_type="whale", magnitude=0.8, urgency=0.5,
    )
    assert alert_high is not None
    assert alert_high.level.value == "orange"

    # mag=0.3, urg=0.3 → YELLOW
    alert_low = src.ingest_llm_factor(
        timestamp_ms=ts_ms + 1, message="whale move small",
        llm_event_type="whale", magnitude=0.3, urgency=0.3,
    )
    assert alert_low is not None
    assert alert_low.level.value == "yellow"


def test_ingest_to_temp_db_populates_tg_events(tmp_path):
    """End-to-end: run a tiny synthetic tg_factors.db through the ingest pipeline."""
    # Build a synthetic tg_factors.db with full LLM factor columns
    tg_db = tmp_path / "tg_factors.db"
    conn = sqlite3.connect(str(tg_db))
    conn.executescript(
        """
        CREATE TABLE tg_factors (
            id INTEGER PRIMARY KEY,
            ts TEXT,
            symbols TEXT,
            direction REAL,
            magnitude REAL,
            urgency REAL,
            confidence REAL,
            halflife_min REAL,
            event_type TEXT,
            reasoning TEXT
        );
        INSERT INTO tg_factors (ts, symbols, direction, magnitude, urgency, confidence,
                                halflife_min, event_type, reasoning) VALUES
          ('2026-01-01 10:00:00.000000', '["*"]', 0.0, 0.1, 0.1, 0.9, 60, 'market',
           'noise: bot idle log'),
          ('2026-01-01 11:00:00.000000', '["*"]', -0.5, 0.8, 0.7, 0.9, 120, 'regulatory',
           'SEC announces enforcement action against exchange'),
          ('2026-01-01 12:00:00.000000', '["BTC"]', 0.1, 0.2, 0.2, 0.5, 60, 'listing',
           'BTC price moved slightly, no event');
        """
    )
    conn.commit()
    conn.close()

    macro_db = tmp_path / "macro_events.db"
    # Initialize macro_events.db schema (EventGuard needs the table to exist)
    from selene_space_alpha.event_guard.tg_source import _ensure_schema  # type: ignore

    conn = sqlite3.connect(str(macro_db))
    _ensure_schema(conn)
    conn.commit()
    conn.close()

    # Re-import with patched default paths
    import importlib
    import tg_ingest_to_events as ingest_mod  # type: ignore

    importlib.reload(ingest_mod)

    # Run ingest against temp DBs
    sys.argv = [
        "tg_ingest_to_events.py",
        "--tg-db", str(tg_db),
        "--macro-db", str(macro_db),
    ]
    rc = ingest_mod.main()
    assert rc == 0

    # Verify: 1 row ingested (regulatory → REGULATORY orange, because mag=0.8>=0.7).
    # market event_type is NOT in the actionable mapping → dropped.
    # noise: reasoning is filtered out by the WHERE clause.
    conn = sqlite3.connect(str(macro_db))
    count = conn.execute("SELECT COUNT(*) FROM tg_events").fetchone()[0]
    assert count == 1, f"expected 1 ingested, got {count}"
    row = conn.execute(
        "SELECT event_type, alert_level, llm_event_type, magnitude, urgency "
        "FROM tg_events"
    ).fetchone()
    assert row[0] == "REGULATORY", f"event_type got {row[0]}"
    assert row[1] == "orange", f"alert_level got {row[1]}"
    assert row[2] == "regulatory", f"llm_event_type got {row[2]}"
    assert abs(row[3] - 0.8) < 0.01, f"magnitude got {row[3]}"
    assert abs(row[4] - 0.7) < 0.01, f"urgency got {row[4]}"
    conn.close()
