"""Tests for fusion strategy signal builders (Phase 5.4)."""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from selene_space_strategy.factory import (
    SIGNAL_BUY,
    SIGNAL_SELL,
    SIGNAL_HOLD,
    build_fusion_strategy_signals,
    get_fusion_strategies,
    get_fusion_strategy_info,
    FUSION_STRATEGIES,
)


def _make_df(
    n: int = 200,
    base_price: float = 100.0,
    trend: float = 0.0,
    volatility: float = 1.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame for testing."""
    np.random.seed(seed)
    close = [base_price]
    for _ in range(n - 1):
        close.append(close[-1] + trend + np.random.normal(0, volatility))
    close = np.array(close)
    high = close + np.random.uniform(0, volatility, n)
    low = close - np.random.uniform(0, volatility, n)
    open_ = close + np.random.normal(0, volatility * 0.3, n)
    volume = np.random.uniform(100, 1000, n)
    timestamps = np.arange(n) * 3600000 + 1710997200000

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class TestFusionStrategyRegistry:
    def test_get_fusion_strategies_returns_all(self) -> None:
        keys = get_fusion_strategies()
        assert len(keys) == 4
        assert "rsi_macd_confluence" in keys
        assert "macd_adx_filter" in keys
        assert "bollinger_rsi_combo" in keys
        assert "triple_confirmation" in keys

    def test_get_fusion_strategy_info(self) -> None:
        info = get_fusion_strategy_info("rsi_macd_confluence")
        assert info is not None
        assert "description" in info
        assert "params" in info
        assert "logic" in info

    def test_get_fusion_strategy_info_unknown(self) -> None:
        assert get_fusion_strategy_info("nonexistent") is None


class TestBuildFusionStrategySignals:
    def test_unknown_strategy_raises(self) -> None:
        df = _make_df(50)
        with pytest.raises(ValueError, match="Unknown fusion strategy"):
            build_fusion_strategy_signals("nonexistent_strategy", df)

    def test_all_strategies_dispatch(self) -> None:
        df = _make_df(200, trend=0.1, volatility=2.0)
        for name in FUSION_STRATEGIES:
            result = build_fusion_strategy_signals(name, df)
            assert isinstance(result, pd.DataFrame)
            assert "signal" in result.columns

    def test_signal_values_valid(self) -> None:
        df = _make_df(200, trend=0.1, volatility=2.0)
        for name in FUSION_STRATEGIES:
            result = build_fusion_strategy_signals(name, df)
            valid_signals = result["signal"].dropna()
            assert set(valid_signals.unique()).issubset(
                {SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD}
            ), f"{name} produced invalid signal values"

    def test_param_overrides(self) -> None:
        df = _make_df(200)
        result = build_fusion_strategy_signals(
            "rsi_macd_confluence", df,
            rsi_period=10, macd_fast=8,
        )
        assert isinstance(result, pd.DataFrame)
        assert "signal" in result.columns


class TestRSIMACDConfluence:
    def test_returns_dataframe_with_indicators(self) -> None:
        df = _make_df(200, trend=0.1, volatility=2.0)
        result = build_fusion_strategy_signals("rsi_macd_confluence", df)
        assert "signal" in result.columns
        assert "rsi" in result.columns
        assert "macd_hist" in result.columns

    def test_rsi_range(self) -> None:
        df = _make_df(200)
        result = build_fusion_strategy_signals("rsi_macd_confluence", df)
        valid_rsi = result["rsi"].dropna()
        assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()

    def test_no_crash_on_flat_data(self) -> None:
        df = _make_df(50, trend=0.0, volatility=0.01)
        result = build_fusion_strategy_signals("rsi_macd_confluence", df)
        assert isinstance(result, pd.DataFrame)

    def test_buy_when_oversold_and_momentum_up(self) -> None:
        """Craft data where RSI is low and MACD histogram is positive."""
        n = 100
        np.random.seed(99)
        # Sharp drop then bounce — RSI will be low, MACD histogram turning positive
        close = np.concatenate([
            np.linspace(100, 70, 40),   # drop
            np.linspace(70, 80, 30),    # bounce
            np.linspace(80, 85, 30),    # continuation
        ])
        high = close + 0.5
        low = close - 0.5
        df = pd.DataFrame({
            "open": close, "high": high, "low": low, "close": close,
            "volume": np.ones(n) * 100,
        })
        result = build_fusion_strategy_signals(
            "rsi_macd_confluence", df,
            rsi_oversold=40, rsi_overbought=60,
        )
        # Should produce at least some BUY signals during the bounce
        buy_signals = (result["signal"] == SIGNAL_BUY).sum()
        assert buy_signals > 0, "Expected BUY signals during oversold bounce"


class TestMACDADXFilter:
    def test_returns_dataframe_with_indicators(self) -> None:
        df = _make_df(200, trend=0.2, volatility=2.0)
        result = build_fusion_strategy_signals("macd_adx_filter", df)
        assert "signal" in result.columns
        assert "adx" in result.columns
        assert "macd_hist" in result.columns

    def test_no_signals_in_flat_market_with_high_threshold(self) -> None:
        """Flat market + high ADX threshold should produce zero signals."""
        df = _make_df(100, trend=0.0, volatility=0.1)
        result = build_fusion_strategy_signals(
            "macd_adx_filter", df, adx_threshold=80,
        )
        buy_count = (result["signal"] == SIGNAL_BUY).sum()
        sell_count = (result["signal"] == SIGNAL_SELL).sum()
        # ADX will be very low in flat market, so no signals should pass filter
        assert buy_count + sell_count == 0

    def test_signals_in_trending_market(self) -> None:
        """Strong trend should produce MACD+ADX signals."""
        df = _make_df(300, trend=0.3, volatility=1.5)
        result = build_fusion_strategy_signals(
            "macd_adx_filter", df, adx_threshold=20,
        )
        total = (result["signal"] != SIGNAL_HOLD).sum()
        assert total > 0, "Expected signals in trending market"


class TestBollingerRSICombo:
    def test_returns_dataframe_with_indicators(self) -> None:
        df = _make_df(200, trend=0.0, volatility=3.0)
        result = build_fusion_strategy_signals("bollinger_rsi_combo", df)
        assert "signal" in result.columns
        assert "bb_upper" in result.columns
        assert "bb_lower" in result.columns
        assert "rsi" in result.columns

    def test_bollinger_band_ordering(self) -> None:
        df = _make_df(200)
        result = build_fusion_strategy_signals("bollinger_rsi_combo", df)
        valid = result["bb_upper"].notna() & result["bb_lower"].notna()
        assert (result.loc[valid, "bb_upper"] >= result.loc[valid, "bb_middle"]).all()
        assert (result.loc[valid, "bb_lower"] <= result.loc[valid, "bb_middle"]).all()

    def test_custom_params(self) -> None:
        df = _make_df(200)
        result = build_fusion_strategy_signals(
            "bollinger_rsi_combo", df,
            bb_period=15, bb_std=1.5, rsi_period=10,
        )
        assert isinstance(result, pd.DataFrame)
        assert "signal" in result.columns


class TestTripleConfirmation:
    def test_returns_dataframe_with_indicators(self) -> None:
        df = _make_df(300, trend=0.0, volatility=3.0)
        result = build_fusion_strategy_signals("triple_confirmation", df)
        assert "signal" in result.columns
        assert "rsi" in result.columns
        assert "macd_hist" in result.columns
        assert "bb_lower" in result.columns

    def test_stricter_than_rsi_macd(self) -> None:
        """Triple confirmation should produce fewer or equal signals than RSI+MACD."""
        df = _make_df(300, trend=0.0, volatility=3.0)
        triple = build_fusion_strategy_signals("triple_confirmation", df)
        rsi_macd = build_fusion_strategy_signals("rsi_macd_confluence", df)
        triple_active = (triple["signal"] != SIGNAL_HOLD).sum()
        rsi_macd_active = (rsi_macd["signal"] != SIGNAL_HOLD).sum()
        # Triple requires all 3 to agree, so should be <= RSI+MACD
        assert triple_active <= rsi_macd_active + 5  # small tolerance for edge effects

    def test_no_false_signals_in_calm_market(self) -> None:
        """Very low volatility should produce zero triple-confirmation signals."""
        df = _make_df(100, trend=0.0, volatility=0.01)
        result = build_fusion_strategy_signals("triple_confirmation", df)
        active = (result["signal"] != SIGNAL_HOLD).sum()
        assert active == 0


class TestSignalConsistency:
    def test_output_length_matches_input(self) -> None:
        df = _make_df(200)
        for name in FUSION_STRATEGIES:
            result = build_fusion_strategy_signals(name, df)
            assert len(result) == len(df), f"{name}: output length mismatch"

    def test_no_nan_in_signal_column(self) -> None:
        df = _make_df(200, trend=0.1, volatility=2.0)
        for name in FUSION_STRATEGIES:
            result = build_fusion_strategy_signals(name, df)
            assert result["signal"].notna().all(), f"{name}: NaN in signal column"

    def test_hold_is_default(self) -> None:
        """Most bars should be HOLD (no signal)."""
        df = _make_df(200, trend=0.0, volatility=1.0)
        for name in FUSION_STRATEGIES:
            result = build_fusion_strategy_signals(name, df)
            hold_pct = (result["signal"] == SIGNAL_HOLD).sum() / len(result)
            assert hold_pct > 0.5, f"{name}: too many active signals ({1 - hold_pct:.0%})"
