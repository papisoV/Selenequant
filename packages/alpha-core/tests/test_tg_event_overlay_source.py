"""Tests for TGEventOverlaySource — de-risk overlay, no directional signals.

Covers (US-004 acceptance):
- given a synthetic tg_factors.db, the source emits position-reduction overlays
  with NO buy/sell (side/direction) key
- non-whitelisted listing/whale events are skipped by default
- macro/security/market are always on
- confidence/magnitude gate matches the event gate
- enrich_klines attaches event_overlay_mult
- build_signals raises NotImplementedError (de-risk has no direction side)
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest


def _make_db(path, rows: list[dict]) -> None:
    """Create a synthetic tg_factors.db matching the export schema."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tg_factors (
            id INTEGER PRIMARY KEY,
            msg_id INTEGER,
            ts TEXT,
            symbols TEXT,
            direction REAL,
            magnitude REAL,
            urgency REAL,
            confidence REAL,
            halflife_min REAL,
            event_type TEXT,
            reasoning TEXT,
            created_at TEXT
        )
        """
    )
    for r in rows:
        conn.execute(
            """INSERT INTO tg_factors
            (msg_id, ts, symbols, direction, magnitude, urgency, confidence,
             halflife_min, event_type, reasoning, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["msg_id"], r["ts"], r["symbols"], r.get("direction", 0.0),
                r["magnitude"], r.get("urgency", 0.5), r["confidence"],
                r.get("halflife_min", 120), r["event_type"],
                r.get("reasoning", ""), r.get("created_at", r["ts"]),
            ),
        )
    conn.commit()
    conn.close()


def _klines() -> pd.DataFrame:
    """Two 4h bars: one at the event ts (in ±24h window), one far outside."""
    idx = pd.to_datetime(["2026-01-01 12:00:00", "2026-02-01 12:00:00"])
    n = 2
    df = pd.DataFrame(
        {
            "open": np.linspace(100, 102, n),
            "high": np.linspace(101, 103, n),
            "low": np.linspace(99, 101, n),
            "close": np.linspace(100.5, 102.5, n),
            "volume": np.linspace(1000, 1100, n),
        },
        index=idx,
    )
    return df


def _overlay(path, **kwargs):
    from selene_space_alpha.factors.tg_event_overlay_source import TGEventOverlaySource

    return TGEventOverlaySource(tg_db_path=str(path), symbol="BTC/USDT", **kwargs)


def test_overlay_emits_de_risk_without_direction(tmp_path):
    """macro + security (always-on) emit an overlay; no side/direction key."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.7, "confidence": 0.85, "event_type": "macro"},
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["*"]',
         "magnitude": 0.6, "confidence": 0.5, "event_type": "security"},
    ])
    source = _overlay(db)
    dicts = source.build_signal_dicts(_klines())

    # Both events hit the same bar → one overlay marker, labeled by the
    # highest-confidence gated event (macro 0.85 > security 0.5).
    assert len(dicts) == 1
    expected_ms = pd.Timestamp("2026-01-01 12:00:00").value // 10**6
    d = dicts[0]
    assert "side" not in d
    assert "direction" not in d
    assert d["position_multiplier"] == 0.5
    assert d["timestamp"] == expected_ms
    assert d["event_type"] == "macro"


def test_listing_whale_off_by_default(tmp_path):
    """listing/whale events are skipped unless whitelisted per-symbol."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "whale"},
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.6, "confidence": 0.7, "event_type": "listing"},
    ])
    source = _overlay(db)
    assert source.build_signal_dicts(_klines()) == []


def test_market_always_on_by_default(tmp_path):
    """market (price-breakout) events de-risk by default — no whitelist needed.

    2026-08-14 forward-vol research (tg_event_forward_vol_2026-08-14.py):
    market is the only type with robust forward persistence on BTC
    (24h 1.19x p=0.002 / 48h 1.13x p=0.004). De-risk stays flat (binary 0.5),
    no magnitude grading — matching the "policy alignment" production decision.
    """
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "market"},
    ])
    source = _overlay(db)
    dicts = source.build_signal_dicts(_klines())
    assert len(dicts) == 1
    d = dicts[0]
    assert "side" not in d
    assert "direction" not in d
    assert d["position_multiplier"] == 0.5
    assert d["event_type"] == "market"


def test_whale_whitelist_enables_whale(tmp_path):
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "whale"},
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["ETH"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "whale"},
    ])
    # BTC whitelist enables the BTC whale; the ETH-only whale stays off for BTC.
    source = _overlay(db, whale_whitelist={"BTC"})
    dicts = source.build_signal_dicts(_klines())
    assert len(dicts) == 1
    assert dicts[0]["event_type"] == "whale"
    assert "side" not in dicts[0]
    assert "direction" not in dicts[0]


def test_listing_whitelist_enables_listing(tmp_path):
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.6, "confidence": 0.7, "event_type": "listing"},
    ])
    source = _overlay(db, listing_whitelist={"BTC"})
    dicts = source.build_signal_dicts(_klines())
    assert len(dicts) == 1
    assert dicts[0]["event_type"] == "listing"


def test_confidence_magnitude_gate(tmp_path):
    """Low-confidence events excluded; magnitude<=0 excluded (event gate parity)."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.9, "confidence": 0.1, "event_type": "macro"},  # low conf
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.0, "confidence": 0.9, "event_type": "macro"},  # zero mag
        {"msg_id": 3, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.7, "confidence": 0.85, "event_type": "macro"},  # valid
    ])
    source = _overlay(db)
    dicts = source.build_signal_dicts(_klines())
    assert len(dicts) == 1
    assert dicts[0]["confidence"] == 0.85


def test_alt_only_listing_skipped(tmp_path):
    """Listing of a non-universe alt (symbols=['ALT']) maps to nothing → skipped."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["ALT"]',
         "magnitude": 0.6, "confidence": 0.7, "event_type": "listing"},
    ])
    source = _overlay(db, listing_whitelist={"BTC"})
    assert source.build_signal_dicts(_klines()) == []


def test_enrich_klines_attaches_mult(tmp_path):
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.7, "confidence": 0.85, "event_type": "macro"},
    ])
    source = _overlay(db)
    enriched = source.enrich_klines(_klines())
    assert enriched["event_overlay_mult"].tolist() == [0.5, 1.0]


def test_build_signals_not_implemented(tmp_path):
    """De-risk has no direction side — cannot be expressed as pydantic Signal."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.7, "confidence": 0.85, "event_type": "macro"},
    ])
    source = _overlay(db)
    with pytest.raises(NotImplementedError):
        source.build_signals(_klines())
