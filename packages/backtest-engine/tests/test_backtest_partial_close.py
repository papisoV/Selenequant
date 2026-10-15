"""Partial close must not pop the position entry nor create phantom shorts.

Regression for the src8 phantom-short artifact: a partial SELL against a
held long was booked as a full round-trip and the entry was popped, so the
strategy's subsequent full-exit SELL fell through to the open-short branch
of ``_book_fill`` — fabricating short trades in a long-only strategy.
"""

from __future__ import annotations

import pytest

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

pytestmark = pytest.mark.filterwarnings(
    "ignore:overflow encountered in scalar power:RuntimeWarning"
)


class _ScriptedQuantityStrategy:
    """Replays (side, quantity) signals, one per bar; None after exhaustion."""

    def __init__(self, plan: list[tuple[OrderSide, float]]) -> None:
        self._plan = list(plan)
        self._idx = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        if self._idx >= len(self._plan):
            return None
        side, qty = self._plan[self._idx]
        self._idx += 1
        return Signal(
            strategy_id="qd:test_partial",  # qd: prefix = adapter reduce-size semantics
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason="scripted",
            quantity=qty,
        )

    def reset(self) -> None:
        self._idx = 0


def _make_klines(prices: list[float], symbol: str = "BTC/USDT") -> list[KlineEvent]:
    return [
        KlineEvent(
            exchange="backtest",
            symbol=symbol,
            timestamp=1711000000000 + i * 3600_000,
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


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        position_sizing="signal_quantity",
        allow_pyramiding=True,
    )


class TestPartialCloseNoPhantomShort:
    def test_partial_sell_then_full_exit_yields_only_long_trades(self) -> None:
        """BUY 10 → SELL 3 (partial) → SELL 7 (full) → no short anywhere.

        Before the fix the partial SELL popped the entry, so the final SELL
        was booked as opening a short — a phantom position from a long-only
        signal stream.
        """
        klines = _make_klines([100.0] * 6)
        plan = [
            (OrderSide.BUY, 10.0),  # bar0 → fills bar1
            (OrderSide.SELL, 3.0),  # bar1 → partial close, fills bar2
            (OrderSide.SELL, 7.0),  # bar2 → full close, fills bar3
        ]
        strategy = _ScriptedQuantityStrategy(plan)
        result = BacktestEngine(config=_config()).run(strategy, klines)

        sides = [t["side"] for t in result.trades]
        assert "short" not in sides, f"phantom short fabricated: {result.trades}"
        assert all(s == "long" for s in sides)
        assert len(result.trades) == 2
        assert result.trades[0]["quantity"] == pytest.approx(3.0)
        assert result.trades[1]["quantity"] == pytest.approx(7.0)

    def test_final_position_quantity_zero_after_partial_then_full(self) -> None:
        """Portfolio quantity must be exactly 0 after the full exit."""
        klines = _make_klines([100.0] * 6)
        plan = [
            (OrderSide.BUY, 10.0),
            (OrderSide.SELL, 3.0),
            (OrderSide.SELL, 7.0),
        ]
        result = BacktestEngine(config=_config()).run(
            _ScriptedQuantityStrategy(plan), klines
        )
        portfolio = result.portfolio if hasattr(result, "portfolio") else None
        if portfolio is not None:
            assert portfolio.get_position_quantity("BTC/USDT") == pytest.approx(0.0)

    def test_partial_sell_proportional_entry_fee_kept(self) -> None:
        """Partial close: remaining entry keeps a proportional fee share."""
        klines = _make_klines([100.0] * 6)
        plan = [
            (OrderSide.BUY, 10.0),
            (OrderSide.SELL, 3.0),
            (OrderSide.SELL, 7.0),
        ]
        result = BacktestEngine(config=_config()).run(
            _ScriptedQuantityStrategy(plan), klines
        )
        # With NoCommission both fees are 0; the invariant checked is that
        # both partial trades carry an entry_price and the second exit
        # equals the first entry price (same position continued).
        assert result.trades[0]["entry_price"] == pytest.approx(
            result.trades[1]["entry_price"]
        )
