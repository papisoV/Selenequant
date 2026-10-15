"""Tests for TGVolRegimeSource — independent volatility-regime risk-budget feed.

Covers (pre-registered acceptance 1 + 6):
- whitelisted (symbol, event_type) combo → 0.85 inside 48h forward window
- non-whitelisted types/symbols don't trigger (1.0)
- gate (conf>=0.3 & mag>0) applies
- enrich_klines attaches vol_regime_mult (orthogonal to event_overlay_mult)
- build_signals raises NotImplementedError (regime feed has no direction side)
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
    """Three 4h bars: at event ts, +12h (in 48h window), +4d (outside 48h)."""
    idx = pd.to_datetime([
        "2026-01-01 12:00:00",  # at event
        "2026-01-02 00:00:00",  # +12h, in window
        "2026-01-05 12:00:00",  # +4d, outside 48h
    ])
    n = 3
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


def _regime(path, symbol: str = "BTC/USDT", **kwargs):
    from selene_space_alpha.factors.tg_vol_regime_source import TGVolRegimeSource

    return TGVolRegimeSource(tg_db_path=str(path), symbol=symbol, **kwargs)


def test_regime_marks_whitelisted_combo(tmp_path):
    """market-BTC (whitelisted) → BTC source marks 48h forward window 0.85."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "market"},
    ])
    source = _regime(db, symbol="BTC/USDT")
    enriched = source.enrich_klines(_klines())
    # bar at event (>= ts) and +12h (<= ts+48h) reduced; +4d outside → 1.0
    assert enriched["vol_regime_mult"].tolist() == [0.85, 0.85, 1.0]

    dicts = source.build_signal_dicts(_klines())
    assert len(dicts) == 2
    for d in dicts:
        assert "side" not in d
        assert "direction" not in d
        assert d["position_multiplier"] == 0.85
        assert d["event_type"] == "market"


def test_whitelist_excludes_other_types(tmp_path):
    """BTC whitelist is only {market}; a macro event on BTC does not trigger."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "macro"},
    ])
    source = _regime(db, symbol="BTC/USDT")
    enriched = source.enrich_klines(_klines())
    assert (enriched["vol_regime_mult"] == 1.0).all()
    assert source.build_signal_dicts(_klines()) == []


def test_macro_only_on_eth_sol(tmp_path):
    """ETH/SOL whitelists are {macro}; market does NOT trigger there."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["ETH"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "macro"},
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["ETH"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "market"},
    ])
    eth = _regime(db, symbol="ETH/USDT")
    eth_macro = eth.enrich_klines(_klines())
    assert eth_macro["vol_regime_mult"].iloc[0] == 0.85

    # market event on ETH is not whitelisted → only macro contributes;
    # one dict per in-window bar (bar at event + bar at +12h), all labeled macro
    dicts = eth.build_signal_dicts(_klines())
    assert len(dicts) == 2
    assert all(d["event_type"] == "macro" for d in dicts)

    # same market event under the SOL source (macro whitelist) → no trigger
    sol_db = tmp_path / "sol.db"
    _make_db(sol_db, [
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["SOL"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "market"},
    ])
    sol = _regime(sol_db, symbol="SOL/USDT")
    assert sol.build_signal_dicts(_klines()) == []


def test_gate_applies(tmp_path):
    """Low-confidence / zero-magnitude events do not trigger (event gate parity)."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.9, "confidence": 0.1, "event_type": "market"},  # low conf
        {"msg_id": 2, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.0, "confidence": 0.9, "event_type": "market"},  # zero mag
    ])
    source = _regime(db, symbol="BTC/USDT")
    enriched = source.enrich_klines(_klines())
    assert (enriched["vol_regime_mult"] == 1.0).all()
    assert source.build_signal_dicts(_klines()) == []


def test_enrich_attaches_column(tmp_path):
    """vol_regime_mult column attached with 1.0 outside the window."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.7, "confidence": 0.85, "event_type": "market"},
    ])
    source = _regime(db, symbol="BTC/USDT")
    enriched = source.enrich_klines(_klines())
    assert "vol_regime_mult" in enriched.columns
    assert enriched["vol_regime_mult"].tolist() == [0.85, 0.85, 1.0]


def test_orthogonal_to_event_overlay_mult(tmp_path):
    """Acceptance 6: vol_regime_mult is independent of the overlay's column.

    Running regime enrich on already-overlay-enriched data must preserve the
    overlay column untouched (separate columns, separate consumers).
    """
    from selene_space_alpha.factors.tg_event_overlay_source import TGEventOverlaySource

    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.8, "confidence": 0.9, "event_type": "market"},
    ])
    regime = _regime(db, symbol="BTC/USDT")
    overlay = TGEventOverlaySource(tg_db_path=str(db), symbol="BTC/USDT")

    first = regime.enrich_klines(_klines())
    both = overlay.enrich_klines(first)
    # regime column preserved; overlay column added independently
    assert both["vol_regime_mult"].tolist() == [0.85, 0.85, 1.0]
    assert both["event_overlay_mult"].tolist() == [0.5, 0.5, 1.0]

    # reverse order also fine — regime adds its column without clobbering overlay
    first2 = overlay.enrich_klines(_klines())
    both2 = regime.enrich_klines(first2)
    assert both2["event_overlay_mult"].tolist() == [0.5, 0.5, 1.0]
    assert both2["vol_regime_mult"].tolist() == [0.85, 0.85, 1.0]


def test_build_signals_not_implemented(tmp_path):
    """A regime feed has no direction side — cannot be a pydantic Signal."""
    db = tmp_path / "tg_factors.db"
    _make_db(db, [
        {"msg_id": 1, "ts": "2026-01-01 12:00:00", "symbols": '["BTC"]',
         "magnitude": 0.7, "confidence": 0.85, "event_type": "market"},
    ])
    source = _regime(db, symbol="BTC/USDT")
    with pytest.raises(NotImplementedError):
        source.build_signals(_klines())
