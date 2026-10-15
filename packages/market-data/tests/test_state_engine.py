"""State-engine coverage — kline→DataFrame conversion, the ATR/ADX/momentum
indicators, classifier mappings, and MarketStateEngine.analyze."""

from __future__ import annotations

import pytest

from selene_space_market.state_engine import (
    InsufficientDataError,
    MarketStateEngine,
    classify_regime,
    classify_trend_strength,
    classify_volatility,
    compute_adx_proxy,
    compute_atr,
    compute_momentum,
    klines_to_dataframe,
)
from selene_space_types.agent import MarketRegime, MarketState
from selene_space_types.market import KlineEvent

SYMBOL = "BTC/USDT"
BASE_TS = 1711000000000


def _candle(price: float, i: int, spread: float, vol: float) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol=SYMBOL,
        timestamp=BASE_TS + i * 3600000,
        interval="1h",
        open=price - spread / 10,
        high=price + spread,
        low=price - spread,
        close=price,
        volume=vol,
        closed=True,
    )


def _trending(n: int = 30, base: float = 67000.0) -> list[KlineEvent]:
    """Steady climb of +50 per bar."""
    return [_candle(base + i * 50.0, i, spread=100.0, vol=1000.0 + i * 10) for i in range(n)]


def _ranging(n: int = 30, base: float = 67000.0) -> list[KlineEvent]:
    """Alternating +/-50 oscillation around base."""
    return [_candle(base + (50.0 if i % 2 == 0 else -50.0), i, spread=20.0, vol=1000.0) for i in range(n)]


class TestKlinesToDataframe:
    def test_column_layout(self) -> None:
        df = klines_to_dataframe(_trending(5))
        assert len(df) == 5
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    def test_empty_input_rejected(self) -> None:
        with pytest.raises(InsufficientDataError):
            klines_to_dataframe([])

    def test_input_order_does_not_matter(self) -> None:
        shuffled = list(reversed(_trending(5)))
        assert klines_to_dataframe(shuffled)["timestamp"].is_monotonic_increasing


class TestIndicatorColumns:
    def _df(self):
        return klines_to_dataframe(_trending(30))

    def test_atr_nonnegative_full_length(self) -> None:
        atr = compute_atr(self._df())
        assert len(atr) == 30
        assert all(atr >= 0)

    def test_adx_proxy_nonnegative_full_length(self) -> None:
        adx = compute_adx_proxy(self._df())
        assert len(adx) == 30
        assert all(adx >= 0)

    def test_momentum_full_length(self) -> None:
        assert len(compute_momentum(self._df())) == 30


class TestClassifierMappings:
    @pytest.mark.parametrize(
        ("adx", "expected"),
        [
            (10.0, MarketRegime.RANGING),
            (20.0, MarketRegime.VOLATILE),  # between ranging/trending thresholds
            (30.0, MarketRegime.TRENDING),
        ],
    )
    def test_regime_bands(self, adx: float, expected: MarketRegime) -> None:
        assert classify_regime(adx, 5.0 if expected is MarketRegime.TRENDING else 0.0) == expected

    def test_trend_strength_scale_and_clamp(self) -> None:
        assert classify_trend_strength(0.0) == 0.0
        assert classify_trend_strength(50.0) == 0.5
        assert classify_trend_strength(100.0) == 1.0
        assert classify_trend_strength(150.0) == 1.0  # clamped above max

    def test_volatility_scale_and_clamp(self) -> None:
        assert classify_volatility(0.0) == 0.0
        assert 0.0 < classify_volatility(0.01) < 0.5
        assert classify_volatility(0.05) == 1.0  # at HIGH threshold
        assert classify_volatility(0.1) == 1.0  # clamped


class TestMarketStateEngine:
    def test_analyze_produces_bounded_state(self) -> None:
        state = MarketStateEngine().analyze(SYMBOL, _trending(30))
        assert isinstance(state, MarketState)
        assert state.symbol == SYMBOL
        assert 0.0 <= state.trend_strength <= 1.0
        assert 0.0 <= state.volatility <= 1.0
        assert state.regime in list(MarketRegime)

    def test_analyze_accepts_prebuilt_dataframe(self) -> None:
        df = klines_to_dataframe(_trending(30))
        assert isinstance(MarketStateEngine().analyze(SYMBOL, df), MarketState)

    def test_short_history_rejected(self) -> None:
        with pytest.raises(InsufficientDataError):
            MarketStateEngine().analyze(SYMBOL, _trending(5))

    def test_ranging_series_yields_weak_trend(self) -> None:
        state = MarketStateEngine().analyze(SYMBOL, _ranging(30))
        assert isinstance(state, MarketState)
        assert state.trend_strength < 0.8

    def test_state_stamps_timestamp(self) -> None:
        assert MarketStateEngine().analyze(SYMBOL, _trending(30)).timestamp > 0
