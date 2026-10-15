"""End-to-end smoke test for MTF runtime aggregation.

Covers US-005 acceptance: MTF e2e —
(a) 1h+15m confluence reaches threshold → emits
(b) 15m-only signal with no 1h trend confirmation → filtered (returns None)

This test is a superset of test_mtf_runtime_integration.py — it focuses on
the end-to-end signal flow through MTFRuntimeAggregator using realistic
kline streams.
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
from selene_space_types.trading import OrderSide


def _sma_cross_compiled(interval: str):
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


def _kline(close: float, interval: str, timestamp: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=timestamp,
        interval=interval,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
        closed=True,
    )


class TestMTFE2E:
    def test_confluence_emits_on_synchronized_rally(self) -> None:
        """1h+15m synchronized decline-then-rally → confluence → emit."""
        main_rt = StrategyRuntime(_sma_cross_compiled("1h"))
        sec_rt = StrategyRuntime(_sma_cross_compiled("15m"))
        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.5,
            require_trend_agreement=False,
        )
        agg = MTFRuntimeAggregator(main_rt, mtf, {"15m": sec_rt})

        # Phase 1: 15h downtrend (1h) + 4x 15m bars per hour, same direction
        for h in range(15):
            price_1h = 100.0 - h * 2
            prev_price = 100.0 - (h - 1) * 2 if h > 0 else price_1h
            for q in range(4):
                ts = h * 3600_000 + q * 900_000
                frac = (q + 1) / 4.0
                price = prev_price + (price_1h - prev_price) * frac
                assert agg.on_kline(_kline(price, "15m", ts)) is None
            agg.on_kline(_kline(price_1h, "1h", h * 3600_000))

        # Phase 2: 15h sharp rally synchronized across both timeframes
        emitted = []
        for i in range(15, 30):
            h = i
            price_1h = 70.0 + (i - 15) * 5
            prev_price = 70.0 + (i - 1 - 15) * 5 if i > 15 else (100.0 - 14 * 2)
            for q in range(4):
                ts = h * 3600_000 + q * 900_000
                frac = (q + 1) / 4.0
                price = prev_price + (price_1h - prev_price) * frac
                assert agg.on_kline(_kline(price, "15m", ts)) is None
            sig = agg.on_kline(_kline(price_1h, "1h", h * 3600_000))
            if sig is not None:
                emitted.append(sig)

        assert len(emitted) >= 1
        assert all(s.side in (OrderSide.BUY, OrderSide.SELL) for s in emitted)

    def test_no_emission_without_trend_confirmation(self) -> None:
        """Only 15m klines (no 1h trend) → no emission (trend disagreement filter)."""
        main_rt = StrategyRuntime(_sma_cross_compiled("1h"))
        sec_rt = StrategyRuntime(_sma_cross_compiled("15m"))
        mtf = MTFConfluence(
            frames={
                "1h": (TimeframeRole.TREND, 0.6),
                "15m": (TimeframeRole.ENTRY, 0.4),
            },
            min_confluence=0.7,
            require_trend_agreement=True,
        )
        agg = MTFRuntimeAggregator(main_rt, mtf, {"15m": sec_rt})

        # Feed ONLY 15m klines — trend (1h) frame stays empty
        emitted = []
        for i in range(60):
            ts = i * 900_000
            price = 100.0 + i * 0.5
            sig = agg.on_kline(_kline(price, "15m", ts))
            if sig is not None:
                emitted.append(sig)

        assert emitted == []
