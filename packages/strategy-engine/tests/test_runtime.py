"""StrategyRuntime coverage — bar buffering, cross-driven signals, RSI
oversold entries, P7 risk exits, and P1-4 per-symbol state buckets."""

from __future__ import annotations

from selene_space_strategy.compiler import compile
from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    ExitRules,
    RiskParams,
)
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


def _candle(close: float, timestamp: int = 0, closed: bool = True, symbol: str = "BTC/USDT") -> KlineEvent:
    """Hourly candle around the given close."""
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=timestamp,
        interval="1h",
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
        closed=closed,
    )


def _compile_sma_cross():
    """Long-only SMA(5)/SMA(10) crossover."""
    comparator = {"indicator": "sma", "params": {"period": 10}}
    config = EngineStrategyConfig(
        id="sma-cross-test",
        name="SMA Cross Test",
        type="sma_cross",
        symbols=["BTC/USDT"],
        interval="1h",
        parsed_entry_rules=EntryRules(
            long=[
                ConditionRule(
                    indicator="sma",
                    params={"period": 5},
                    operator="crosses_above",
                    comparator=comparator,
                )
            ]
        ),
        parsed_exit_rules=ExitRules(
            close_long=[
                ConditionRule(
                    indicator="sma",
                    params={"period": 5},
                    operator="crosses_below",
                    comparator=comparator,
                )
            ]
        ),
    )
    return compile(config)


def _compile_rsi_reversal():
    """RSI(14) buy-below-30 / sell-above-70."""
    config = EngineStrategyConfig(
        id="rsi-test",
        name="RSI Test",
        type="rsi_reversal",
        symbols=["BTC/USDT"],
        interval="1h",
        parsed_entry_rules=EntryRules(
            long=[
                ConditionRule(
                    indicator="rsi", params={"period": 14}, operator="less_than", comparator=30.0
                )
            ]
        ),
        parsed_exit_rules=ExitRules(
            close_long=[
                ConditionRule(
                    indicator="rsi",
                    params={"period": 14},
                    operator="greater_than",
                    comparator=70.0,
                )
            ]
        ),
    )
    return compile(config)


WARM_BARS = [
    {"timestamp": 0, "open": 99, "high": 101, "low": 98, "close": 100, "volume": 100},
    {"timestamp": 3600000, "open": 99, "high": 101, "low": 98, "close": 100, "volume": 100},
]


def _open_long_at_100(rt: StrategyRuntime) -> None:
    """Seed the BTC bucket into an open long priced at 100."""
    state = rt._get_state("BTC/USDT")
    state.position = "long"
    state.entry_price = 100.0
    state.bars = [dict(bar) for bar in WARM_BARS]


class TestBarAccounting:
    """Buffer growth, trimming and gating."""

    def test_partial_candle_discarded(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        assert rt.on_kline(_candle(100.0, closed=False)) is None
        assert rt.bar_count == 0

    def test_bars_grow_with_feed(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        for i in range(10):
            rt.on_kline(_candle(100.0 + i, timestamp=i * 1000))
        assert rt.bar_count == 10

    def test_buffer_cap_enforced(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross(), max_bars=5)
        for i in range(20):
            rt.on_kline(_candle(100.0 + i, timestamp=i * 1000))
        assert rt.bar_count == 5

    def test_newborn_runtime_is_flat(self) -> None:
        assert StrategyRuntime(_compile_sma_cross()).position == "flat"

    def test_lone_bar_cannot_signal(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        assert rt.on_kline(_candle(100.0, timestamp=0)) is None

    def test_reset_restores_virgin_state(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        for i in range(10):
            rt.on_kline(_candle(100.0 + i, timestamp=i * 1000))
        rt.reset()
        assert rt.bar_count == 0 and rt.position == "flat"


class TestCrossSignals:
    """Golden/death cross driven trades."""

    def test_recovery_after_slide_crosses_up(self) -> None:
        """Slide keeps SMA(5)<SMA(10); rebound forces a BUY through."""
        rt = StrategyRuntime(_compile_sma_cross())

        for i in range(15):
            rt.on_kline(_candle(100.0 - i * 2, timestamp=i * 3600000))
        assert rt.position == "flat"

        emitted = []
        for i in range(15, 30):
            price = 70.0 + (i - 15) * 5
            sig = rt.on_kline(_candle(price, timestamp=i * 3600000))
            if sig is not None:
                emitted.append(sig)

        assert len(emitted) >= 1
        assert emitted[0].side == OrderSide.BUY
        assert rt.position == "long"

    def test_rally_then_slump_round_trip(self) -> None:
        """One full cycle emits both a BUY and a SELL."""
        rt = StrategyRuntime(_compile_sma_cross())
        emitted = []

        for i in range(12):  # plateau equalises both SMAs
            rt.on_kline(_candle(100.0, timestamp=i * 3600000))

        for i in range(12, 25):  # rally leg
            price = 100.0 + (i - 12) * 5
            sig = rt.on_kline(_candle(price, timestamp=i * 3600000))
            if sig:
                emitted.append(sig)

        for i in range(25, 45):  # slump leg
            price = 165.0 - (i - 25) * 6
            sig = rt.on_kline(_candle(price, timestamp=i * 3600000))
            if sig:
                emitted.append(sig)

        sides = {s.side for s in emitted}
        assert OrderSide.BUY in sides and OrderSide.SELL in sides

    def test_first_emission_has_complete_payload(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        for i in range(30):
            sig = rt.on_kline(_candle(100.0 + i * 3, timestamp=i * 3600000))
            if sig is not None:
                assert isinstance(sig, Signal)
                assert sig.strategy_id == "sma-cross-test"
                assert sig.symbol == "BTC/USDT"
                assert sig.reason
                assert sig.timestamp > 0
                return


class TestOversoldPath:
    """RSI reversal entry."""

    def test_thirty_bar_slide_yields_buy(self) -> None:
        rt = StrategyRuntime(_compile_rsi_reversal())
        buys = []
        for i in range(30):
            sig = rt.on_kline(_candle(100.0 - i * 2, timestamp=i * 3600000))
            if sig and sig.side == OrderSide.BUY:
                buys.append(sig)
        assert len(buys) >= 1


class TestRiskExitsP7:
    """Stop-loss / take-profit enforcement."""

    def test_five_percent_stop_fires_on_six_percent_drop(self) -> None:
        rt = StrategyRuntime(
            _compile_sma_cross(), risk_params=RiskParams(stop_loss_pct=0.05)
        )
        _open_long_at_100(rt)

        sig = rt.on_kline(_candle(94.0, timestamp=7200000))
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert "Stop loss" in sig.reason
        assert rt.position == "flat"

    def test_ten_percent_target_fires_on_eleven_percent_pop(self) -> None:
        rt = StrategyRuntime(
            _compile_sma_cross(), risk_params=RiskParams(take_profit_pct=0.10)
        )
        _open_long_at_100(rt)

        sig = rt.on_kline(_candle(111.0, timestamp=7200000))
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert "Take profit" in sig.reason
        assert rt.position == "flat"

    def test_small_drift_does_not_exit(self) -> None:
        risk = RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10)
        rt = StrategyRuntime(_compile_sma_cross(), risk_params=risk)
        _open_long_at_100(rt)

        sig = rt.on_kline(_candle(103.0, timestamp=7200000))
        if sig is not None:
            assert "Stop loss" not in sig.reason
            assert "Take profit" not in sig.reason


class TestSymbolBucketIsolationP14:
    """Interleaved symbols keep independent bars/position/entry."""

    def test_bars_partition_by_symbol(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        rt.on_kline(_candle(100.0, symbol="BTC/USDT", timestamp=0))
        rt.on_kline(_candle(50.0, symbol="ETH/USDT", timestamp=0))
        rt.on_kline(_candle(101.0, symbol="BTC/USDT", timestamp=3600000))

        btc, eth = rt._get_state("BTC/USDT"), rt._get_state("ETH/USDT")
        assert len(btc.bars) == 2 and len(eth.bars) == 1
        assert btc.bars[0]["close"] == 100.0
        assert eth.bars[0]["close"] == 50.0

    def test_long_on_btc_leaves_eth_flat(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        btc, eth = rt._get_state("BTC/USDT"), rt._get_state("ETH/USDT")
        btc.position = "long"
        btc.entry_price = 100.0

        assert btc.position == "long"
        assert eth.position == "flat" and eth.entry_price == 0.0

    def test_reset_drops_every_bucket(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        rt.on_kline(_candle(100.0, symbol="BTC/USDT", timestamp=0))
        rt.on_kline(_candle(50.0, symbol="ETH/USDT", timestamp=0))
        assert len(rt._states) == 2

        rt.reset()
        assert rt._states == {}
        assert rt._last_symbol is None
        assert rt.bar_count == 0 and rt.position == "flat"

    def test_legacy_properties_mirror_latest_symbol(self) -> None:
        rt = StrategyRuntime(_compile_sma_cross())
        rt.on_kline(_candle(100.0, symbol="BTC/USDT", timestamp=0))
        rt.on_kline(_candle(50.0, symbol="ETH/USDT", timestamp=0))

        assert rt.bar_count == 1  # ETH arrived last
        assert rt.position == "flat"
