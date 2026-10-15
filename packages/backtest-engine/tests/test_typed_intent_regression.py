"""P1-4 typed-intent regression: explicit TradeIntent contract through the engine.

锚 A2:策略返回显式 TradeIntent(order_type=STOP_MARKET, risk_params.stop_price=X)
      → 回测 trade 记录 exit_action="sell"、按止损触发。
锚 C1:同一 TradeIntent 实例喂 BacktestEngine 与适配器 → 两边解析出相同
      (side, order_type, price, stop_price) 四元组(公共 helper 单测)。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))

import pytest

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.trading import OrderSide, OrderType
from selene_space_types.agent import TradeIntent


_STAMP = 1711000000000


def _bar(
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1_000_000.0,
    ts: int = _STAMP,
) -> KlineEvent:
    return KlineEvent(
        exchange="backtest",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="1h",
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


def _intent(
    side: OrderSide,
    order_type: OrderType,
    *,
    price: float | None = None,
    stop_price: float | None = None,
    quantity: float = 1.0,
) -> TradeIntent:
    risk_params = {"stop_price": stop_price} if stop_price is not None else {}
    return TradeIntent(
        symbol="BTC/USDT",
        side=side,
        quantity=quantity,
        price=price,
        order_type=order_type,
        reasoning="P1-4 typed intent test",
        confidence=1.0,
        risk_params=risk_params,
        timestamp=_STAMP,
    )


class _TypedIntentStrategy:
    """Emits an explicit TradeIntent for a BUY open then a STOP_MARKET close."""

    config = None

    def __init__(self) -> None:
        self._step = 0

    def on_kline(self, event: KlineEvent) -> TradeIntent | None:
        if self._step == 0:
            self._step += 1
            return _intent(OrderSide.BUY, OrderType.MARKET, quantity=1.0)
        if self._step == 1:
            self._step += 1
            return _intent(
                OrderSide.SELL,
                OrderType.STOP_MARKET,
                stop_price=98.5,
                quantity=1.0,
            )
        return None

    def reset(self) -> None:
        self._step = 0


class TestTypedIntentBacktest:
    """A2:显式 TradeIntent 经回测引擎按止损触发,记录完整往返。"""

    def test_stop_market_intent_produces_stop_exit(self) -> None:
        bars = [
            _bar(100.0, 101.0, 99.0, 100.0, ts=0),  # 信号根:BUY market + STOP 平多意图
            _bar(99.5, 100.5, 98.0, 99.0, ts=1),  # low 98 <= stop 98.5 → 触发
            _bar(99.0, 99.5, 98.5, 99.2, ts=2),  # 收尾
        ]
        engine = BacktestEngine(
            BacktestConfig(
                position_sizing="signal_quantity",
                lookahead_guard=False,
                symbol="BTC/USDT",
            )
        )
        result = engine.run(_TypedIntentStrategy(), bars)
        closes = [
            t
            for t in result.trades
            if t["side"] == "long" and t["exit_action"] == "sell"
        ]
        assert closes, "expected a long round trip closed by the stop exit"
        assert result.trades_count == 1

    def test_intent_market_price_fills_next_bar_open(self) -> None:
        """MARKET intent(price=None)在信号根后下一根开盘成交。"""
        bars = [
            _bar(100.0, 101.0, 99.0, 100.0, ts=0),
            _bar(101.0, 102.0, 100.0, 101.5, ts=1),
            _bar(102.0, 103.0, 101.0, 102.5, ts=2),
        ]

        class _BuyThenSell:
            config = None

            def __init__(self) -> None:
                self._step = 0

            def on_kline(self, event: KlineEvent) -> TradeIntent | None:
                if self._step == 0:
                    self._step += 1
                    return _intent(OrderSide.BUY, OrderType.MARKET, quantity=1.0)
                if self._step == 1:
                    self._step += 1
                    return _intent(OrderSide.SELL, OrderType.MARKET, quantity=1.0)
                return None

            def reset(self) -> None:
                self._step = 0

        engine = BacktestEngine(
            BacktestConfig(
                position_sizing="signal_quantity",
                lookahead_guard=False,
                symbol="BTC/USDT",
                slippage=NoSlippage(),
                commission=NoCommission(),
            )
        )
        result = engine.run(_BuyThenSell(), bars)
        assert result.trades_count == 1
        # entry fills at bar-1 open (101.0), exit at bar-2 open (102.0)
        assert float(result.trades[0]["entry_price"]) == pytest.approx(101.0)
        assert float(result.trades[0]["exit_price"]) == pytest.approx(102.0)
