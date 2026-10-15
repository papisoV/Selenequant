"""SimulatedBroker fill mechanics: market execution, friction, limit triggers."""

from __future__ import annotations

import pytest

from selene_space_backtest.broker import SimulatedBroker
from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.slippage import FixedSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.trading import Order, OrderSide, OrderStatus, OrderType

_ORDER_ID = "test-ord-001"
_STAMP = 1711000000000


def _bar(
    close: float = 100.0,
    low: float = 95.0,
    high: float = 105.0,
    open_price: float = 98.0,
) -> KlineEvent:
    return KlineEvent(
        exchange="backtest",
        symbol="BTC/USDT",
        timestamp=_STAMP,
        interval="1h",
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        closed=True,
    )


def _ticket(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: float = 1.0,
    price: float | None = None,
) -> Order:
    return Order(
        id=_ORDER_ID,
        symbol="BTC/USDT",
        side=side,
        type=order_type,
        quantity=quantity,
        price=price,
        created_at=_STAMP,
        updated_at=_STAMP,
    )


class TestMarketExecution:
    def test_market_order_fills_on_bar_open(self) -> None:
        broker = SimulatedBroker()
        ticket = _ticket()
        bar = _bar(close=67000.0, open_price=66500.0)

        fill = broker.execute(ticket, bar)

        assert fill is not None
        assert fill.price == 66500.0
        assert fill.quantity == 1.0
        assert ticket.status == OrderStatus.FILLED

    def test_buy_price_slips_upward(self) -> None:
        broker = SimulatedBroker(slippage=FixedSlippage(bps=10))
        bar = _bar(close=10000.0, open_price=10000.0)

        fill = broker.execute(_ticket(), bar)

        assert fill is not None
        # Decimal-priced fill (H19): compare in the float domain.
        assert float(fill.price) == 10000.0 * 1.001

    def test_fee_scales_with_notional(self) -> None:
        broker = SimulatedBroker(commission=PercentageCommission(rate=0.001))

        fill = broker.execute(
            _ticket(quantity=2.0), _bar(close=50000.0, open_price=50000.0)
        )

        assert fill is not None
        assert fill.fee == 50000.0 * 2.0 * 0.001


class TestLimitTriggering:
    def test_buy_limit_touches_low(self) -> None:
        broker = SimulatedBroker()
        # low 95 <= limit 96 → triggerable
        fill = broker.execute(
            _ticket(side=OrderSide.BUY, order_type=OrderType.LIMIT, price=96.0),
            _bar(low=95.0),
        )

        assert fill is not None
        assert fill.price == 96.0

    def test_buy_limit_never_reached(self) -> None:
        broker = SimulatedBroker()
        # low 95 > limit 90 → no touch
        fill = broker.execute(
            _ticket(side=OrderSide.BUY, order_type=OrderType.LIMIT, price=90.0),
            _bar(low=95.0),
        )

        assert fill is None

    def test_sell_limit_touches_high(self) -> None:
        broker = SimulatedBroker()
        # high 105 >= limit 104 → triggerable
        fill = broker.execute(
            _ticket(side=OrderSide.SELL, order_type=OrderType.LIMIT, price=104.0),
            _bar(high=105.0),
        )

        assert fill is not None
        assert fill.price == 104.0

    def test_sell_limit_never_reached(self) -> None:
        broker = SimulatedBroker()

        fill = broker.execute(
            _ticket(side=OrderSide.SELL, order_type=OrderType.LIMIT, price=110.0),
            _bar(high=105.0),
        )

        assert fill is None


class TestMaxCostCap:
    def test_buy_capped_by_available_cash(self) -> None:
        broker = SimulatedBroker()
        # 2 units * 66500 = 133000 > max_cost 100000 → capped at ~1.5038
        fill = broker.execute(
            _ticket(quantity=2.0),
            _bar(close=67000.0, open_price=66500.0),
            max_cost=100_000.0,
        )

        assert fill is not None
        assert float(fill.quantity) <= 100_000.0 / 66_500.0 + 1e-9
        assert float(fill.price) * float(fill.quantity) <= 100_000.0 + 1e-6

    def test_tiny_budget_fills_affordable_fraction(self) -> None:
        broker = SimulatedBroker()
        # crypto 数量本就是分数:100 预算 @ 66500 → 0.0015 单位,合法小数成交
        fill = broker.execute(
            _ticket(quantity=1.0),
            _bar(close=67000.0, open_price=66500.0),
            max_cost=100.0,
        )

        assert fill is not None
        assert float(fill.quantity) == pytest.approx(100.0 / 66_500.0, abs=1e-12)
        assert float(fill.price) * float(fill.quantity) <= 100.0 + 1e-9

    def test_zero_cash_buy_returns_none(self) -> None:
        broker = SimulatedBroker()
        # affordable=0 → 无可成交,返回 None(残单由引擎留队重试)
        fill = broker.execute(
            _ticket(quantity=1.0),
            _bar(close=67000.0, open_price=66500.0),
            max_cost=0.0,
        )

        assert fill is None

    def test_cap_includes_commission(self) -> None:
        broker = SimulatedBroker(commission=PercentageCommission(rate=0.001))
        # affordable = 100000 / (66500 * 1.001) ≈ 1.5023 units
        fill = broker.execute(
            _ticket(quantity=5.0),
            _bar(close=67000.0, open_price=66500.0),
            max_cost=100_000.0,
        )

        assert fill is not None
        expected = 100_000.0 / (66_500.0 * 1.001)
        assert float(fill.quantity) == pytest.approx(expected, abs=1e-9)
        assert float(fill.price) * float(fill.quantity) + float(fill.fee) <= (
            100_000.0 + 1e-6
        )
