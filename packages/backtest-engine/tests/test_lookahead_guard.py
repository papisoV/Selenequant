"""Lookahead-bias guard: future-timestamped signals must be caught.

Two symptoms the guard watches for:
1. A Signal stamped later than its kline (the strategy peeked ahead).
2. An implausibly high Sharpe with enough trades, even with clean stamps.
"""


from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class _Alternator:
    """Base: alternate BUY/SELL per bar; subclasses decide the stamp."""

    stamp_offset_ms = 0

    def __init__(self) -> None:
        self._bar = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._bar += 1
        side = OrderSide.BUY if self._bar % 2 == 1 else OrderSide.SELL
        return Signal(
            strategy_id="alternating",
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp + self.stamp_offset_ms,
            reason=f"bar {self._bar} {side.value}",
        )

    def reset(self) -> None:
        self._bar = 0


class _CleanStamps(_Alternator):
    """Signals stamped exactly at the kline — no lookahead."""

    stamp_offset_ms = 0


class _FutureStamps(_Alternator):
    """Signals stamped one bar into the future — the cheat under test."""

    stamp_offset_ms = 3_600_000


def _session(prices: list[float]) -> list[KlineEvent]:
    origin = 1711000000000
    hour = 3_600_000
    return [
        KlineEvent(
            exchange="backtest",
            symbol="BTC/USDT",
            timestamp=origin + i * hour,
            interval="1h",
            open=p,
            high=p * 1.01,
            low=p * 0.99,
            close=p,
            volume=100.0,
            closed=True,
        )
        for i, p in enumerate(prices)
    ]


def _guarded_engine() -> BacktestEngine:
    config = BacktestConfig(
        initial_cash=10000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        lookahead_guard=True,
    )
    return BacktestEngine(config=config)


# Oscillating tape keeps the honest strategy's Sharpe modest so only the
# timestamp symptom can fire.
_CHOPPING_TAPE = [
    100.0, 102.0, 99.0, 103.0, 98.0, 101.0, 97.0, 104.0,
    96.0, 102.0, 99.0, 105.0, 97.0, 101.0, 98.0, 103.0,
    96.0, 100.0, 97.0, 102.0, 99.0, 104.0, 97.0, 101.0,
    98.0, 103.0, 96.0, 100.0, 99.0, 102.0,
]


class TestLookaheadGuard:
    def test_clean_stamps_stay_silent(self) -> None:
        result = _guarded_engine().run(_CleanStamps(), _session(_CHOPPING_TAPE))
        assert result.lookahead_warnings == [], (
            f"unexpected warnings: {result.lookahead_warnings}"
        )

    def test_future_stamps_raise_warnings(self) -> None:
        climbing = [100.0 + i for i in range(20)]
        result = _guarded_engine().run(_FutureStamps(), _session(climbing))
        assert len(result.lookahead_warnings) > 0
        assert any("future data" in w for w in result.lookahead_warnings)

    def test_guard_off_lets_cheating_slide(self) -> None:
        config = BacktestConfig(
            initial_cash=10000.0,
            commission=NoCommission(),
            slippage=NoSlippage(),
            lookahead_guard=False,
        )
        climbing = [100.0 + i for i in range(20)]
        result = BacktestEngine(config=config).run(_FutureStamps(), _session(climbing))
        assert result.lookahead_warnings == []
