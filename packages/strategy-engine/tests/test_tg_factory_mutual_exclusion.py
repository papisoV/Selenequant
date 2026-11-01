"""TG source factory mutual-exclusion guard (US-007).

use_event_overlay_source=True must route every build to TGEventOverlaySource
and must NEVER construct the legacy TGSpikeSource in the same call — both TG
sources active in one session would double de-risk.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
sys.path.insert(0, str(REPO / "packages" / "strategy-engine"))

import pandas as pd  # noqa: E402


def _klines(n=30):
    idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": [int(t.value // 10**6) for t in idx],
            "open": 100.0, "high": 100.0, "low": 100.0,
            "close": 100.0, "volume": 1.0,
        },
        index=idx,
    )


def _empty_tg_db(tmp_path):
    """Create tg_factors.db with the schema load_tg_signals SELECTs, no rows."""
    p = tmp_path / "tg_factors.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE tg_factors (id INTEGER PRIMARY KEY, msg_id INTEGER, ts TEXT, "
        "symbols TEXT, direction REAL, magnitude REAL, urgency REAL, "
        "confidence REAL, halflife_min REAL, event_type TEXT, reasoning TEXT)"
    )
    conn.commit()
    conn.close()
    return str(p)


def _track_legacy(monkeypatch):
    """Record TGSpikeSource construction without blocking it."""
    from selene_space_alpha.factors import tg_spike_source as tss

    built: list[str] = []
    real_init = tss.TGSpikeSource.__init__

    def _tracking_init(self, *args, **kwargs):
        built.append("TGSpikeSource")
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(tss.TGSpikeSource, "__init__", _tracking_init)
    return built


def test_overlay_mode_never_instantiates_legacy_source(tmp_path, monkeypatch):
    """use_event_overlay_source=True → TGSpikeSource.__init__ must not run."""
    from selene_space_strategy import factory as fac

    built = _track_legacy(monkeypatch)
    db = _empty_tg_db(tmp_path)
    out = fac.build_tg_spike_signals(
        _klines(), symbol="BTC/USDT", interval="4h",
        tg_db_path=db, use_event_overlay_source=True,
    )
    assert built == [], "legacy TGSpikeSource must not be constructed in overlay mode"
    # Emitted overlay dicts are de-risk only — no side/direction key.
    if out:
        assert "side" not in out[0]
        assert "direction" not in out[0]


def test_overlay_portfolio_never_instantiates_legacy_source(tmp_path, monkeypatch):
    """build_tg_spike_portfolio_signals overlay path must not touch TGSpikeSource."""
    from selene_space_strategy import factory as fac

    built = _track_legacy(monkeypatch)
    db = _empty_tg_db(tmp_path)
    out = fac.build_tg_spike_portfolio_signals(
        {"BTC/USDT": _klines(), "ETH/USDT": _klines()},
        interval="4h", tg_db_path=db, use_event_overlay_source=True,
    )
    assert built == [], "legacy TGSpikeSource must not be constructed in overlay mode"
    assert set(out) == {"BTC/USDT", "ETH/USDT"}
    if out["BTC/USDT"]:
        assert "side" not in out["BTC/USDT"][0]


def test_legacy_mode_still_builds_spike_source(tmp_path, monkeypatch):
    """Default (use_event_overlay_source=False) still routes to TGSpikeSource."""
    from selene_space_strategy import factory as fac

    built = _track_legacy(monkeypatch)
    db = _empty_tg_db(tmp_path)
    fac.build_tg_spike_signals(
        _klines(), symbol="BTC/USDT", interval="4h", tg_db_path=db,
    )
    assert built == ["TGSpikeSource"]
