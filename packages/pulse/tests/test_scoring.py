from __future__ import annotations

from ai_market_pulse.scoring import score_asset


def test_bullish_metrics_score_high_and_low_risk() -> None:
    metrics = {
        "last_close": 110,
        "sma20": 100,
        "sma50": 95,
        "sma200": 90,
        "rsi14": 55,
        "macd": 1.0,
        "macd_signal": 0.5,
        "return_20d": 0.07,
        "return_60d": 0.10,
        "drawdown_60d": -0.03,
        "atr_pct": 0.02,
        "volume_ratio_20d": 1.6,
    }

    signal = score_asset(metrics)

    assert signal.score > 70
    assert signal.stance in {"constructive", "watch bullish"}
    assert signal.risk_level == "low"
    assert signal.reasons


def test_bearish_metrics_score_low_and_high_risk() -> None:
    metrics = {
        "last_close": 80,
        "sma20": 90,
        "sma50": 95,
        "sma200": 100,
        "rsi14": 82,
        "macd": -1.0,
        "macd_signal": 0.0,
        "return_20d": -0.08,
        "return_60d": -0.12,
        "drawdown_60d": -0.25,
        "atr_pct": 0.07,
    }

    signal = score_asset(metrics)

    assert signal.score < 35
    assert signal.risk_level == "high"


def test_empty_metrics_are_neutral() -> None:
    signal = score_asset({})

    assert signal.score == 50
    assert signal.stance == "neutral"
    assert signal.reasons == []


def test_score_is_clipped_to_0_100() -> None:
    signal = score_asset(
        {
            "last_close": 1_000_000,
            "sma20": 1,
            "sma50": 1,
            "sma200": 1,
            "return_20d": 10,
            "return_60d": 10,
            "volume_ratio_20d": 5,
        }
    )

    assert 0 <= signal.score <= 100


def test_zero_sma200_is_treated_as_a_valid_value_not_missing() -> None:
    metrics = {
        "last_close": 110,
        "sma20": 100,
        "sma50": 95,
        "sma200": 0.0,
    }

    signal = score_asset(metrics)

    assert "Long-term trend remains above the 200-day average." in signal.reasons


def test_reasons_are_capped() -> None:
    metrics = {
        "last_close": 110,
        "sma20": 100,
        "sma50": 95,
        "sma200": 90,
        "rsi14": 55,
        "macd": 1.0,
        "macd_signal": 0.5,
        "return_20d": 0.07,
        "return_60d": 0.10,
        "drawdown_60d": -0.2,
        "atr_pct": 0.05,
        "volume_ratio_20d": 1.6,
    }

    signal = score_asset(metrics)

    assert len(signal.reasons) <= 6
