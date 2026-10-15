"""P1-4 B 组锚:SignalDrivenExecutionAdapter 把 TradeIntent 直驱执行引擎。

B1: adapter.on_kline(TradeIntent) → engine.place_order(account_id, quantity,
    price, stop_price=risk_params["stop_price"], side, order_type)。
B2: supports_mark_price=True 后端收到 mark_price/signal_timestamp_ms;
    False 后端不收。
B3: 后端 on_fill 回推 → 适配器转回策略可见事件(策略无订阅则忽略不炸)。
B4: stock 后端(无有效 on_* 回推)下单成功、回推为空、无异常。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

import pytest

from app.core.signal_driver import SignalDrivenExecutionAdapter
from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide, OrderType


def _intent(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    *,
    price: float | None = None,
    stop_price: float | None = None,
    quantity: float = 1.0,
    symbol: str = "BTC/USDT",
) -> TradeIntent:
    risk_params = {"stop_price": stop_price} if stop_price is not None else {}
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        order_type=order_type,
        reasoning="signal driver test",
        confidence=1.0,
        risk_params=risk_params,
        timestamp=1711000000000,
    )


class RecordingEngine:
    """记录型 fake engine:断言 kwargs、可选 supports_mark_price 标记。"""

    def __init__(self, supports_mark_price: bool = True) -> None:
        self.supports_mark_price = supports_mark_price
        self.place_order_calls: list[dict] = []
        self.fill_handlers: list = []
        self.order_handlers: list = []
        self.fills_to_push: list = []
        self.last_order: object = None

    async def place_order(self, **kwargs) -> object:
        self.place_order_calls.append(kwargs)
        return kwargs

    def on_fill(self, handler) -> None:
        self.fill_handlers.append(handler)

    def on_order_update(self, handler) -> None:
        self.order_handlers.append(handler)

    def emit_fill(self, fill) -> None:
        for h in self.fill_handlers:
            h(fill)

    def emit_order(self, order) -> None:
        for h in self.order_handlers:
            h(order)


class _NoopStrategy:
    """策略无 on_fill/on_order_update 订阅 → 回推忽略不炸。"""

    def on_fill(self, fill) -> None:  # pragma: no cover
        raise AssertionError("strategy with no subscription should not get called")

    def on_order_update(self, order) -> None:  # pragma: no cover
        raise AssertionError("strategy with no subscription should not get called")


class _RecordingStrategy:
    """订阅 fill/order 事件的策略,记录事件。"""

    def __init__(self) -> None:
        self.fills: list = []
        self.orders: list = []

    def on_fill(self, fill) -> None:
        self.fills.append(fill)

    def on_order_update(self, order) -> None:
        self.orders.append(order)


class TestAdapterPlaceOrderKwargs:
    """B1:adapter.on_kline(TradeIntent) 调 place_order 用记录型 fake 断言 kwargs。"""

    @pytest.mark.asyncio
    async def test_market_intent_kwargs(self) -> None:
        engine = RecordingEngine()
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=None
        )
        intent = _intent(OrderSide.BUY, OrderType.MARKET, quantity=2.0)
        await adapter.on_kline(intent)
        assert len(engine.place_order_calls) == 1
        kw = engine.place_order_calls[0]
        assert kw["account_id"] == "acct-1"
        assert kw["symbol"] == "BTC/USDT"
        assert kw["side"] is OrderSide.BUY
        assert kw["order_type"] is OrderType.MARKET
        assert kw["quantity"] == 2.0
        assert kw["price"] is None
        assert kw["stop_price"] is None

    @pytest.mark.asyncio
    async def test_limit_intent_kwargs(self) -> None:
        engine = RecordingEngine()
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=None
        )
        intent = _intent(OrderSide.BUY, OrderType.LIMIT, price=96.5, quantity=1.0)
        await adapter.on_kline(intent)
        kw = engine.place_order_calls[0]
        assert float(kw["price"]) == pytest.approx(96.5)
        assert kw["stop_price"] is None

    @pytest.mark.asyncio
    async def test_stop_market_intent_kwargs(self) -> None:
        engine = RecordingEngine()
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=None
        )
        intent = _intent(OrderSide.SELL, OrderType.STOP_MARKET, stop_price=98.5)
        await adapter.on_kline(intent)
        kw = engine.place_order_calls[0]
        assert kw["side"] is OrderSide.SELL
        assert kw["order_type"] is OrderType.STOP_MARKET
        assert float(kw["stop_price"]) == pytest.approx(98.5)
        assert kw["price"] is None


class TestAdapterMarkPriceRouting:
    """B2:supports_mark_price 后端收到 mark_price/signal_timestamp_ms;False 不收。"""

    @pytest.mark.asyncio
    async def test_supports_mark_price_true_gets_extra(self) -> None:
        engine = RecordingEngine(supports_mark_price=True)
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=None
        )
        intent = _intent(OrderSide.BUY, OrderType.MARKET)
        await adapter.on_kline(intent)
        kw = engine.place_order_calls[0]
        assert "mark_price" in kw
        assert "signal_timestamp_ms" in kw
        assert kw["signal_timestamp_ms"] == intent.timestamp

    @pytest.mark.asyncio
    async def test_supports_mark_price_false_no_extra(self) -> None:
        engine = RecordingEngine(supports_mark_price=False)
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=None
        )
        intent = _intent(OrderSide.BUY, OrderType.MARKET)
        await adapter.on_kline(intent)
        kw = engine.place_order_calls[0]
        assert "mark_price" not in kw
        assert "signal_timestamp_ms" not in kw


class TestAdapterFillBackpressure:
    """B3:后端 on_fill 回推 → 适配器转回策略可见事件。"""

    @pytest.mark.asyncio
    async def test_fill_routed_to_subscribed_strategy(self) -> None:
        strategy = _RecordingStrategy()
        engine = RecordingEngine()
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=strategy
        )
        adapter._subscribe()

        class _Fill:
            symbol = "BTC/USDT"
            side = "buy"
            price = 100.0
            quantity = 1.0

        engine.emit_fill(_Fill())
        assert len(strategy.fills) == 1

    @pytest.mark.asyncio
    async def test_fill_ignored_when_strategy_has_no_subscription(self) -> None:
        strategy = _NoopStrategy()
        engine = RecordingEngine()
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-1", strategy=strategy
        )
        adapter._subscribe()

        class _Fill:
            symbol = "BTC/USDT"
            side = "buy"
            price = 100.0
            quantity = 1.0

        # Must not raise despite strategy having no on_fill.
        engine.emit_fill(_Fill())


class TestAdapterStockNoopBackend:
    """B4:stock 后端(无有效 on_* 回推)下单成功、回推为空、无异常。"""

    @pytest.mark.asyncio
    async def test_stock_backend_places_order_and_noop_events(self) -> None:
        class StockEngine:
            supports_mark_price = False
            place_calls: int = 0
            fill_handlers: list = []
            order_handlers: list = []

            async def place_order(self, **kwargs) -> dict:
                self.place_calls += 1
                return kwargs

            def on_fill(self, handler) -> None:
                # no-op stub per stock backend
                pass

            def on_order_update(self, handler) -> None:
                pass

        engine = StockEngine()
        strategy = _NoopStrategy()
        adapter = SignalDrivenExecutionAdapter(
            engine=engine, account_id="acct-stock", strategy=strategy
        )
        adapter._subscribe()  # must not raise despite stock's no-op stubs
        intent = _intent(OrderSide.BUY, OrderType.MARKET, symbol="AAPL")
        await adapter.on_kline(intent)
        assert engine.place_calls == 1
        # No backpressure raised.
