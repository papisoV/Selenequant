"""Tests for multi-timeframe signal fusion."""

from __future__ import annotations

import pytest

from selene_space_strategy.mtf import (
    KlineResampler,
    MTFConfluence,
    TimeframeRole,
)
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


def _signal(
    side: OrderSide = OrderSide.BUY,
    symbol: str = "BTC/USDT",
    strength: float = 1.0,
    ts: int = 1711000000000,
) -> Signal:
    return Signal(
        strategy_id="test",
        symbol=symbol,
        side=side,
        strength=strength,
        timestamp=ts,
        reason="test",
    )


def _kline(
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
    ts: int = 1710997200000,  # Aligned to hour boundary
) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="15m",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


class TestMTFConfluence:
    def test_full_confluence_buy(self) -> None:
        """All frames agree on BUY → confluence = 1.0."""
        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.7,
        )
        mtf.update_signal("1h", _signal(OrderSide.BUY))
        mtf.update_signal("15m", _signal(OrderSide.BUY))

        result = mtf.evaluate()
        assert result is not None
        assert result.confluence_score == pytest.approx(1.0)
        assert result.signal.side == OrderSide.BUY
        assert result.trend_direction == OrderSide.BUY
        assert result.entry_direction == OrderSide.BUY

    def test_full_confluence_sell(self) -> None:
        """All frames agree on SELL → confluence = 1.0."""
        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.7,
        )
        mtf.update_signal("1h", _signal(OrderSide.SELL))
        mtf.update_signal("15m", _signal(OrderSide.SELL))

        result = mtf.evaluate()
        assert result is not None
        assert result.signal.side == OrderSide.SELL

    def test_no_confluence_opposing_signals(self) -> None:
        """Frames disagree → confluence below threshold."""
        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.7,
            require_trend_agreement=True,
        )
        mtf.update_signal("1h", _signal(OrderSide.BUY))
        mtf.update_signal("15m", _signal(OrderSide.SELL))

        # Trend says BUY but entry says SELL → requires trend agreement, so None
        result = mtf.evaluate()
        assert result is None

    def test_partial_confluence_with_three_frames(self) -> None:
        """2 out of 3 frames agree → partial confluence."""
        mtf = MTFConfluence(
            frames={
                "4h": (TimeframeRole.TREND, 0.4),
                "1h": (TimeframeRole.CONFIRM, 0.3),
                "15m": (TimeframeRole.ENTRY, 0.3),
            },
            min_confluence=0.6,
            require_trend_agreement=True,
        )
        mtf.update_signal("4h", _signal(OrderSide.BUY))
        mtf.update_signal("1h", _signal(OrderSide.BUY))
        mtf.update_signal("15m", _signal(OrderSide.SELL))

        result = mtf.evaluate()
        assert result is not None
        # BUY weight = 0.4 + 0.3 = 0.7, SELL = 0.3
        assert result.confluence_score == pytest.approx(0.7)
        assert result.signal.side == OrderSide.BUY

    def test_no_signals_returns_none(self) -> None:
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.5,
        )
        assert mtf.evaluate() is None

    def test_single_frame(self) -> None:
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.5,
        )
        mtf.update_signal("1h", _signal(OrderSide.BUY))
        result = mtf.evaluate()
        assert result is not None
        assert result.confluence_score == pytest.approx(1.0)

    def test_trend_disagreement_blocks_signal(self) -> None:
        """When require_trend_agreement=True, trend frame must agree."""
        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.5,
            require_trend_agreement=True,
        )
        mtf.update_signal("1h", _signal(OrderSide.SELL))
        mtf.update_signal("15m", _signal(OrderSide.BUY))

        # Dominant is SELL (0.6 > 0.4), trend also SELL → allowed
        result = mtf.evaluate()
        assert result is not None
        assert result.signal.side == OrderSide.SELL

    def test_unknown_interval_ignored(self) -> None:
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.5,
        )
        mtf.update_signal("5m", _signal(OrderSide.BUY))  # Unknown interval
        assert mtf.evaluate() is None

    def test_reset_clears_signals(self) -> None:
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.5,
        )
        mtf.update_signal("1h", _signal(OrderSide.BUY))
        mtf.reset()
        assert mtf.evaluate() is None

    def test_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            MTFConfluence(frames={}, min_confluence=0.5)
        with pytest.raises(ValueError):
            MTFConfluence(frames={"1h": (TimeframeRole.TREND, 1.0)}, min_confluence=1.5)


class TestKlineResampler:
    def test_resample_4_bars_to_1h(self) -> None:
        """4 × 15m bars → 1 × 1h bar."""
        resampler = KlineResampler(
            target_interval_ms=3600000,  # 1h
            source_interval_ms=900000,   # 15m
        )
        # 4 bars in the same hour bucket, hour-aligned start
        base_ts = 1710997200000  # Aligned to hour
        results = []
        for i in range(4):
            r = resampler.add_bar(_kline(
                open_=100.0 + i,
                high=105.0 + i,
                low=95.0 - i,
                close=102.0 + i,
                volume=250.0,
                ts=base_ts + i * 900000,
            ))
            if r is not None:
                results.append(r)

        # Should get 1 resampled bar after 4 source bars
        assert len(results) == 1
        bar = results[0]
        assert bar.open == 100.0
        assert bar.close == 105.0  # last close
        assert bar.high == 108.0   # max high
        assert bar.low == 92.0     # min low
        assert bar.volume == pytest.approx(1000.0)  # sum of volumes

    def test_no_emit_before_bucket_complete(self) -> None:
        resampler = KlineResampler(
            target_interval_ms=3600000,
            source_interval_ms=900000,
        )
        base_ts = 1710997200000
        # Only 3 bars (not enough for 1h from 15m)
        for i in range(3):
            r = resampler.add_bar(_kline(ts=base_ts + i * 900000))
            assert r is None  # Not enough bars yet

    def test_flush_partial_bucket(self) -> None:
        resampler = KlineResampler(
            target_interval_ms=3600000,
            source_interval_ms=900000,
        )
        base_ts = 1710997200000
        resampler.add_bar(_kline(ts=base_ts))
        resampler.add_bar(_kline(ts=base_ts + 900000))

        # Only 2 bars, but flush anyway
        result = resampler.flush()
        assert result is not None
        assert result.volume == pytest.approx(2000.0)

    def test_flush_empty(self) -> None:
        resampler = KlineResampler(target_interval_ms=3600000)
        assert resampler.flush() is None

    def test_cross_bucket_resampling(self) -> None:
        """Bars spanning two hour buckets produce two resampled bars."""
        resampler = KlineResampler(
            target_interval_ms=3600000,
            source_interval_ms=900000,
        )
        results = []
        base_ts = 1710997200000  # Hour-aligned

        # First hour: 4 bars
        for i in range(4):
            r = resampler.add_bar(_kline(
                close=100.0 + i,
                ts=base_ts + i * 900000,
            ))
            if r is not None:
                results.append(r)

        # Second hour: 4 bars
        for i in range(4):
            r = resampler.add_bar(_kline(
                close=200.0 + i,
                ts=base_ts + 3600000 + i * 900000,
            ))
            if r is not None:
                results.append(r)

        assert len(results) == 2
        assert results[0].close == 103.0   # last close of first hour
        assert results[1].close == 203.0   # last close of second hour

    def test_invalid_target_interval(self) -> None:
        with pytest.raises(ValueError):
            KlineResampler(target_interval_ms=0)

    def test_reset(self) -> None:
        resampler = KlineResampler(target_interval_ms=3600000, source_interval_ms=900000)
        resampler.add_bar(_kline(ts=1711000000000))
        resampler.reset()
        assert resampler.flush() is None
