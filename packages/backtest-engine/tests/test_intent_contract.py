"""P1-4 C1:公共 helper 把 TradeIntent 解析成执行端/回测端共用四元组。

同一 TradeIntent 实例喂 BacktestEngine 与 SignalDrivenExecutionAdapter →
两边解析出相同 (side, order_type, price, stop_price) 四元组。
helper 是唯一权威解析,回测引擎 typed 直译与适配器都消费它。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))

import pytest

from decimal import Decimal

from selene_space_backtest.engine import BacktestEngine
from selene_space_backtest.intent_contract import resolve_intent_quad
from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide, OrderType


def _as_float(value: Decimal | None) -> float:
    """Type-safe cast for tests: fail loudly if the optional price is missing."""
    assert value is not None, "expected a concrete price/stop_price"
    return float(value)


def _intent(
    side: OrderSide,
    order_type: OrderType,
    *,
    price: float | None = None,
    stop_price: float | None = None,
) -> TradeIntent:
    risk_params = {"stop_price": stop_price} if stop_price is not None else {}
    return TradeIntent(
        symbol="BTC/USDT",
        side=side,
        quantity=1.0,
        price=price,
        order_type=order_type,
        reasoning="C1 helper test",
        confidence=1.0,
        risk_params=risk_params,
        timestamp=1711000000000,
    )


class TestResolveIntentQuad:
    """四元组解析:同一 intent → 回测与适配器读出相同值。"""

    def test_market_intent(self) -> None:
        intent = _intent(OrderSide.BUY, OrderType.MARKET)
        quad = resolve_intent_quad(intent)
        assert quad == (
            OrderSide.BUY,
            OrderType.MARKET,
            None,
            None,
        )

    def test_limit_intent(self) -> None:
        intent = _intent(OrderSide.BUY, OrderType.LIMIT, price=96.5)
        quad = resolve_intent_quad(intent)
        assert quad[0] is OrderSide.BUY
        assert quad[1] is OrderType.LIMIT
        assert _as_float(quad[2]) == pytest.approx(96.5)
        assert quad[3] is None

    def test_stop_market_intent(self) -> None:
        intent = _intent(OrderSide.SELL, OrderType.STOP_MARKET, stop_price=98.5)
        quad = resolve_intent_quad(intent)
        assert quad[0] is OrderSide.SELL
        assert quad[1] is OrderType.STOP_MARKET
        assert quad[2] is None
        assert _as_float(quad[3]) == pytest.approx(98.5)

    def test_stop_limit_intent_both_prices(self) -> None:
        intent = _intent(
            OrderSide.SELL,
            OrderType.STOP_LIMIT,
            price=96.5,
            stop_price=97.0,
        )
        quad = resolve_intent_quad(intent)
        assert quad[0] is OrderSide.SELL
        assert quad[1] is OrderType.STOP_LIMIT
        assert _as_float(quad[2]) == pytest.approx(96.5)
        assert _as_float(quad[3]) == pytest.approx(97.0)

    def test_engine_and_helper_agree(self) -> None:
        """C1 核心:engine typed 直译与 helper 解析一致。"""
        intent = _intent(OrderSide.SELL, OrderType.STOP_MARKET, stop_price=98.5)
        quad = resolve_intent_quad(intent)
        engine_order_type = BacktestEngine._intent_order_type(intent)
        engine_stop = BacktestEngine._intent_stop_price(intent)
        assert engine_order_type is quad[1]
        assert _as_float(engine_stop) == _as_float(quad[3])
