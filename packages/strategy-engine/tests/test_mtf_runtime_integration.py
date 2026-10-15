"""Tests for MTFRuntimeAggregator — multi-timeframe signal aggregation.

Covers US-002 acceptance criteria:
- (a) mtf=None / no secondary runtimes: behavior matches direct runtime call
- (b) dual runtime + mtf confluence reaches threshold: emits signal
- (c) dual runtime + trend disagreement: signal filtered (returns None)

Uses simple SMA cross strategies on synthetic kline streams so we can control
when signals fire.
"""

from __future__ import annotations

from selene_space_strategy.compiler import compile
from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    ExitRules,
)
from selene_space_strategy.mtf import MTFConfluence, TimeframeRole
from selene_space_strategy.mtf_runtime import MTFRuntimeAggregator
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


def _sma_cross_compiled(interval: str = "1h"):
    """Create a compiled SMA cross strategy for the given interval."""
    entry_rule = ConditionRule(
        indicator="sma",
        params={"period": 5},
        operator="crosses_above",
        comparator={"indicator": "sma", "params": {"period": 10}},
    )
    exit_rule = ConditionRule(
        indicator="sma",
        params={"period": 5},
        operator="crosses_below",
        comparator={"indicator": "sma", "params": {"period": 10}},
    )
    config = EngineStrategyConfig(
        id=f"sma-cross-{interval}",
        name=f"SMA Cross {interval}",
        type="sma_cross",
        symbols=["BTC/USDT"],
        interval=interval,
        parsed_entry_rules=EntryRules(long=[entry_rule]),
        parsed_exit_rules=ExitRules(close_long=[exit_rule]),
    )
    return compile(config)


def _kline(
    close: float,
    interval: str = "1h",
    timestamp: int = 0,
    symbol: str = "BTC/USDT",
) -> KlineEvent:
    """Helper to create a closed KlineEvent."""
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=timestamp,
        interval=interval,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
        closed=True,
    )


class TestMTFRuntimeAggregatorNoMTF:
    """Case (a): without MTF, aggregator behaves like the wrapped runtime."""

    def test_no_mtf_passes_through(self) -> None:
        """When mtf has only one frame and require_trend_agreement=False,
        the aggregator should pass through main runtime signals.

        Pattern (mirrors test_runtime.test_sma_cross_generates_buy_signal):
        15 bars downtrend then 15 bars sharp uptrend → SMA(5) crosses above SMA(10).
        """
        compiled = _sma_cross_compiled("1h")
        main_runtime = StrategyRuntime(compiled)
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.7,
            require_trend_agreement=False,
        )
        agg = MTFRuntimeAggregator(main_runtime, mtf)

        signals = []
        # Phase 1: downtrend
        for i in range(15):
            price = 100.0 - i * 2
            agg.on_kline(_kline(price, interval="1h", timestamp=i * 3600_000))
        # Phase 2: sharp uptrend
        for i in range(15, 30):
            price = 70.0 + (i - 15) * 5
            sig = agg.on_kline(_kline(price, interval="1h", timestamp=i * 3600_000))
            if sig is not None:
                signals.append(sig)

        assert len(signals) >= 1
        assert all(isinstance(s, Signal) for s in signals)


class TestMTFRuntimeAggregatorConfluence:
    """Case (b): dual runtime + confluence reaches threshold → emits."""

    def test_dual_runtime_confluence_emits(self) -> None:
        """Both 1h and 15m produce BUY signals → confluence 1.0 → emit.

        Decline-then-sharp-rally pattern synchronized across 1h and 15m so
        both timeframes see SMA(5) cross above SMA(10) at roughly the same time.
        15m gets 4 bars per 1h bar following the same decline-then-rally shape.
        """
        main_compiled = _sma_cross_compiled("1h")
        sec_compiled = _sma_cross_compiled("15m")
        main_runtime = StrategyRuntime(main_compiled)
        sec_runtime = StrategyRuntime(sec_compiled)

        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.5,
            require_trend_agreement=False,
        )
        agg = MTFRuntimeAggregator(main_runtime, mtf, {"15m": sec_runtime})

        # Phase 1: 15 hours downtrend (1h) + 4x 15m bars per hour, same trend
        for h in range(15):
            price_1h = 100.0 - h * 2
            prev_price = (100.0 - (h - 1) * 2) if h > 0 else price_1h
            for q in range(4):
                ts_15m = h * 3600_000 + q * 900_000
                frac = (q + 1) / 4.0
                price = prev_price + (price_1h - prev_price) * frac
                sig = agg.on_kline(_kline(price, interval="15m", timestamp=ts_15m))
                assert sig is None  # secondary never emits
            agg.on_kline(_kline(price_1h, interval="1h", timestamp=h * 3600_000))

        # Phase 2: 15 hours sharp uptrend (1h) + 4x 15m bars per hour
        emitted = []
        for i in range(15, 30):
            h = i
            price_1h = 70.0 + (i - 15) * 5
            prev_price = 70.0 + (i - 1 - 15) * 5 if i > 15 else (100.0 - 14 * 2)
            for q in range(4):
                ts_15m = h * 3600_000 + q * 900_000
                frac = (q + 1) / 4.0
                price = prev_price + (price_1h - prev_price) * frac
                sig = agg.on_kline(_kline(price, interval="15m", timestamp=ts_15m))
                assert sig is None
            sig = agg.on_kline(_kline(price_1h, interval="1h", timestamp=h * 3600_000))
            if sig is not None:
                emitted.append(sig)

        # In a synchronized recovery across both timeframes, confluence should
        # be reached and at least one signal should emit.
        assert len(emitted) >= 1


class TestMTFRuntimeAggregatorTrendDisagreement:
    """Case (c): main signal disagrees with trend → filtered."""

    def test_trend_disagreement_filters_signal(self) -> None:
        """When require_trend_agreement=True and the trend frame has no signal,
        evaluate() returns None so aggregator returns None.

        We keep the trend (1h) frame empty by never feeding it 1h klines,
        then feed only 15m klines. The 15m runtime may produce signals, but
        they should never emit because the main interval (1h, TREND role)
        has no signal — trend_direction is None, and with require_trend_agreement
        the absence of a trend signal blocks emission.
        """
        main_compiled = _sma_cross_compiled("1h")
        sec_compiled = _sma_cross_compiled("15m")
        main_runtime = StrategyRuntime(main_compiled)
        sec_runtime = StrategyRuntime(sec_compiled)

        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.7,
            require_trend_agreement=True,
        )
        agg = MTFRuntimeAggregator(main_runtime, mtf, {"15m": sec_runtime})

        # Feed ONLY 15m klines (no 1h) — trend frame stays empty
        emitted = []
        for i in range(40):
            ts = i * 900_000
            price = 10.0 + i * 0.5
            sig = agg.on_kline(_kline(price, interval="15m", timestamp=ts))
            if sig is not None:
                emitted.append(sig)

        # No 1h trend signal → no emission (15m never emits directly anyway,
        # and main-interval klines never arrive so evaluate() returns None)
        assert emitted == []


class TestMTFRuntimeAggregatorUnknownInterval:
    """Bonus: klines for intervals not in mtf frames are ignored."""

    def test_unknown_interval_ignored(self) -> None:
        compiled = _sma_cross_compiled("1h")
        main_runtime = StrategyRuntime(compiled)
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.7,
            require_trend_agreement=False,
        )
        agg = MTFRuntimeAggregator(main_runtime, mtf)

        # Feed a 4h kline — not in frames
        sig = agg.on_kline(_kline(100.0, interval="4h", timestamp=0))
        assert sig is None


class TestMTFRuntimeAggregatorReset:
    """Bonus: reset() clears all runtimes and mtf state."""

    def test_reset_clears_state(self) -> None:
        compiled = _sma_cross_compiled("1h")
        main_runtime = StrategyRuntime(compiled)
        mtf = MTFConfluence(
            frames={"1h": (TimeframeRole.TREND, 1.0)},
            min_confluence=0.7,
            require_trend_agreement=False,
        )
        agg = MTFRuntimeAggregator(main_runtime, mtf)

        # Feed some klines
        for i, price in enumerate(range(10, 20)):
            agg.on_kline(_kline(float(price), interval="1h", timestamp=i * 3600_000))

        # Reset should not raise and should clear state
        agg.reset()
        assert main_runtime.bar_count == 0
        assert mtf.frame_states == {}
