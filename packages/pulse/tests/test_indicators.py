from __future__ import annotations

import pandas as pd

from ai_market_pulse.indicators import calculate_indicators
from ai_market_pulse.scoring import score_asset


def test_calculate_indicators_has_core_fields() -> None:
    rows = []
    for index in range(80):
        close = 100 + index * 0.5
        rows.append(
            {
                "Date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=index),
                "Open": close - 0.2,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
                "Volume": 1_000_000 + index * 1000,
            }
        )
    history = pd.DataFrame(rows)

    metrics = calculate_indicators(history)

    assert metrics["sma20"] is not None
    assert metrics["sma50"] is not None
    assert metrics["rsi14"] is not None
    assert metrics["return_20d"] is not None
    assert metrics["support_20d"] is not None
    assert metrics["resistance_20d"] is not None


def test_score_asset_returns_bounded_score() -> None:
    metrics = {
        "last_close": 120,
        "sma20": 115,
        "sma50": 110,
        "sma200": 90,
        "rsi14": 58,
        "macd": 1.2,
        "macd_signal": 0.7,
        "return_20d": 0.08,
        "return_60d": 0.15,
        "drawdown_60d": -0.04,
        "atr_pct": 0.02,
        "volume_ratio_20d": 1.6,
    }

    signal = score_asset(metrics)

    assert 0 <= signal.score <= 100
    assert signal.stance in {"constructive", "watch bullish", "neutral", "defensive", "high risk"}
    assert signal.reasons
