"""EventGuard regime-feed integration tests (P4 wiring acceptance 1-4).

Covers the TG volatility-regime feed wired into EventGuard
(get_position_multiplier) and the factory portfolio mirror:

- whitelisted (symbol, event_type) -> 0.85 inside the 48h-forward window,
  1.0 outside
- non-whitelisted combos (macro-BTC, market-ETH, market-SOL) -> 1.0
- get_tg_multiplier does NOT include the regime feed (TG-shock path unchanged)
- enable_tg_regime=False -> regime ignored (pre-wiring behavior preserved)
- macro + regime compose via min() (no compound 0.5 x 0.85)
- factory build_tg_spike_portfolio_signals(enable_tg_regime=True) emits
  0.85 dicts; False does not
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

EV_MS = pd.Timestamp("2026-01-01 12:00:00", tz="UTC").value // 10**6
HOUR_MS = 3_600_000


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


def _guard(tmp_path, tg_db, *, enable_tg: bool = False, enable_tg_regime: bool = True):
    """EventGuard with macro db absent, VIX off; only the regime feed varies."""
    from selene_space_alpha.event_guard.guard import EventGuard

    return EventGuard(
        macro_db_path=str(tmp_path / "macro_events.db"),
        enable_vix=False,
        enable_tg=enable_tg,
        enable_tg_regime=enable_tg_regime,
        tg_regime_db_path=str(tg_db),
    )


def _whitelisted_row(symbol_short: str, event_type: str) -> dict:
    return {
        "msg_id": 1,
        "ts": "2026-01-01 12:00:00",
        "symbols": json.dumps([symbol_short]),
        "magnitude": 0.8,
        "confidence": 0.9,
        "event_type": event_type,
    }


@pytest.mark.parametrize(
    "symbol,event_type",
    [("BTC/USDT", "market"), ("ETH/USDT", "macro"), ("SOL/USDT", "macro")],
)
def test_whitelisted_combos_reduce_in_window(tmp_path, symbol, event_type):
    short = symbol.split("/")[0]
    db = tmp_path / f"{short}.db"
    _make_db(db, [_whitelisted_row(short, event_type)])
    guard = _guard(tmp_path, db)
    assert guard.get_position_multiplier(symbol, EV_MS + HOUR_MS) == 0.85
    # Half-open window: at exactly event_ts + 48h the alert is expired.
    assert guard.get_position_multiplier(symbol, EV_MS + 48 * HOUR_MS) == 1.0


@pytest.mark.parametrize(
    "symbol,event_type",
    [("BTC/USDT", "macro"), ("ETH/USDT", "market"), ("SOL/USDT", "market")],
)
def test_non_whitelisted_combos_no_reduce(tmp_path, symbol, event_type):
    short = symbol.split("/")[0]
    db = tmp_path / f"nw_{short}.db"
    _make_db(db, [_whitelisted_row(short, event_type)])
    guard = _guard(tmp_path, db)
    assert guard.get_position_multiplier(symbol, EV_MS + HOUR_MS) == 1.0


def test_get_tg_multiplier_excludes_regime(tmp_path):
    """Regime lives only in get_position_multiplier, not the TG-shock path."""
    db = tmp_path / "btc.db"
    _make_db(db, [_whitelisted_row("BTC", "market")])
    guard = _guard(tmp_path, db, enable_tg=True)
    assert guard.get_position_multiplier("BTC/USDT", EV_MS + HOUR_MS) == 0.85
    assert guard.get_tg_multiplier("BTC/USDT", EV_MS + HOUR_MS) == 1.0


def test_enable_tg_regime_false_ignores_feed(tmp_path):
    db = tmp_path / "btc.db"
    _make_db(db, [_whitelisted_row("BTC", "market")])
    guard = _guard(tmp_path, db, enable_tg_regime=False)
    assert guard.get_position_multiplier("BTC/USDT", EV_MS + HOUR_MS) == 1.0


def test_macro_and_regime_compose_via_min(tmp_path):
    """A stricter macro alert wins via min(), not a compound 0.5 x 0.85."""
    macro_db = tmp_path / "macro_events.db"
    conn = sqlite3.connect(str(macro_db))
    conn.execute("CREATE TABLE macro_events (event_type TEXT, date TEXT, timestamp_utc INTEGER)")
    conn.execute("INSERT INTO macro_events VALUES (?,?,?)", ("CPI", "2026-01-01", EV_MS))
    conn.commit()
    conn.close()

    db = tmp_path / "btc.db"
    _make_db(db, [_whitelisted_row("BTC", "market")])
    from selene_space_alpha.event_guard.guard import EventGuard

    guard = EventGuard(
        macro_db_path=str(macro_db),
        enable_vix=False,
        enable_tg=False,
        enable_tg_regime=True,
        tg_regime_db_path=str(db),
    )
    # +1h -> CPI RED (±2h window) mult=0.5; regime 0.85; min -> 0.5.
    assert guard.get_position_multiplier("BTC/USDT", EV_MS + HOUR_MS) == 0.5


def _klines() -> pd.DataFrame:
    """Three 4h bars: at event ts, +12h (in 48h window), +4d (outside)."""
    idx = pd.to_datetime([
        "2026-01-01 12:00:00",
        "2026-01-02 00:00:00",
        "2026-01-05 12:00:00",
    ])
    return pd.DataFrame(
        {
            "open": [100.0, 100.5, 101.0],
            "high": [101.0, 101.5, 102.0],
            "low": [99.0, 99.5, 100.0],
            "close": [100.5, 101.0, 101.5],
            "volume": [1000.0, 1050.0, 1100.0],
        },
        index=idx,
    )


def test_factory_portfolio_regime_mirror(tmp_path):
    from selene_space_strategy import factory as fac

    db = tmp_path / "tg_factors.db"
    _make_db(db, [_whitelisted_row("BTC", "market")])
    out = fac.build_tg_spike_portfolio_signals(
        {"BTC/USDT": _klines()},
        interval="4h",
        tg_db_path=str(db),
        use_event_overlay_source=True,
        enable_tg_regime=True,
    )
    regime_dicts = [d for d in out["BTC/USDT"] if d.get("position_multiplier") == 0.85]
    assert regime_dicts, "regime feed must emit 0.85 dicts when enabled"
    for d in regime_dicts:
        assert "side" not in d and "direction" not in d


def test_factory_portfolio_regime_off(tmp_path):
    from selene_space_strategy import factory as fac

    db = tmp_path / "tg_factors.db"
    _make_db(db, [_whitelisted_row("BTC", "market")])
    out = fac.build_tg_spike_portfolio_signals(
        {"BTC/USDT": _klines()},
        interval="4h",
        tg_db_path=str(db),
        use_event_overlay_source=True,
        enable_tg_regime=False,
    )
    assert not any(d.get("position_multiplier") == 0.85 for d in out["BTC/USDT"])
