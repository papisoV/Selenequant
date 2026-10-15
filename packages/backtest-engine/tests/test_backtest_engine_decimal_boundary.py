"""H19 阶段 2C boundary test — backtest-engine constructs Decimal Fill/Position
without TypeError.

Per plan v4 §Expanded Test Plan integration #4, this test ONLY verifies that
the backtest-engine boundary (broker → Fill, engine → _PositionEntry, portfolio
← Fill) crosses the Decimal domain without raising TypeError. It does NOT test
backtest internal trades PnL precision — that is the documented scope
limitation (acceptance #9): backtest internal trades PnL stays Decimal/float
and precision is not asserted.
"""

from __future__ import annotations


import pytest

from selene_space_backtest.broker import SimulatedBroker
from selene_space_backtest.commissions import NoCommission, PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.execution import twap_slice, vwap_slice
from selene_space_backtest.portfolio import Portfolio
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.trading import (
    Fill,
    Order,
    OrderSide,
    OrderType,
)
from decimal import Decimal


def _kline(close: float = 100.0, open_price: float = 100.0, volume: float = 100.0) -> KlineEvent:
    return KlineEvent(
        exchange="backtest",
        symbol="BTC/USDT",
        timestamp=1711000000000,
        interval="1h",
        open=open_price,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=volume,
        closed=True,
    )


def _order(
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.0,
    order_type: OrderType = OrderType.MARKET,
    price: float | None = None,
) -> Order:
    return Order(
        id="test-ord-001",
        symbol="BTC/USDT",
        side=side,
        type=order_type,
        quantity=quantity,
        price=price,
        created_at=1711000000000,
        updated_at=1711000000000,
    )


class TestBrokerFillBoundary:
    """Broker.execute returns Fill whose price/quantity/fee fields are Decimal."""

    def test_market_fill_returns_decimal_fields(self) -> None:
        broker = SimulatedBroker(slippage=NoSlippage(), commission=NoCommission())
        order = _order(quantity=2.0)
        fill = broker.execute(order, _kline(open_price=50000.0))

        assert fill is not None
        # Boundary contract: Fill.price/quantity/fee are Decimal
        assert isinstance(fill.price, Decimal)
        assert isinstance(fill.quantity, Decimal)
        assert isinstance(fill.fee, Decimal)
        # Values round-trip to the expected floats
        assert float(fill.price) == pytest.approx(50000.0)
        assert float(fill.quantity) == pytest.approx(2.0)
        assert float(fill.fee) == pytest.approx(0.0)

    def test_market_fill_with_commission_decimal_fee(self) -> None:
        broker = SimulatedBroker(
            slippage=NoSlippage(),
            commission=PercentageCommission(rate=0.001),
        )
        order = _order(quantity=2.0)
        fill = broker.execute(order, _kline(open_price=50000.0))

        assert fill is not None
        assert isinstance(fill.fee, Decimal)
        # 50000 * 2 * 0.001 = 100
        assert float(fill.fee) == pytest.approx(100.0, rel=1e-6)

    def test_limit_fill_decimal_boundary(self) -> None:
        broker = SimulatedBroker(slippage=NoSlippage(), commission=NoCommission())
        # Buy limit at 96, kline low 95 → triggered, fill at limit price
        order = _order(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=96.0,
        )
        kline = KlineEvent(
            exchange="backtest",
            symbol="BTC/USDT",
            timestamp=1711000000000,
            interval="1h",
            open=98.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=100.0,
            closed=True,
        )
        fill = broker.execute(order, kline)

        assert fill is not None
        assert isinstance(fill.price, Decimal)
        assert float(fill.price) == pytest.approx(96.0)


class TestPortfolioFillBoundary:
    """Portfolio.apply_fill consumes Decimal Fill fields without TypeError."""

    def test_buy_updates_cash_and_position_with_decimal_fill(self) -> None:
        portfolio = Portfolio(initial_cash=10000.0)
        # Construct a Fill with float inputs — the model coerces to Decimal
        fill = Fill(
            id="fill-test",
            order_id="ord-test",
            symbol="BTC/USDT",
            price=50000.0,
            quantity=0.1,
            fee=5.0,
            timestamp=1711000000000,
        )
        # Should not raise TypeError
        portfolio.apply_fill(fill, OrderSide.BUY)

        # Cash: 10000 - (50000*0.1 + 5) = 10000 - 5005 = 4995
        assert portfolio.cash == pytest.approx(4995.0, rel=1e-6)
        assert portfolio.get_position_quantity("BTC/USDT") == pytest.approx(0.1, rel=1e-6)

    def test_sell_updates_cash_and_position_with_decimal_fill(self) -> None:
        portfolio = Portfolio(initial_cash=10000.0)
        # Pre-populate a long position
        buy_fill = Fill(
            id="fill-buy",
            order_id="ord-buy",
            symbol="BTC/USDT",
            price=50000.0,
            quantity=0.1,
            fee=0.0,
            timestamp=1711000000000,
        )
        portfolio.apply_fill(buy_fill, OrderSide.BUY)

        sell_fill = Fill(
            id="fill-sell",
            order_id="ord-sell",
            symbol="BTC/USDT",
            price=51000.0,
            quantity=0.1,
            fee=5.0,
            timestamp=1711000000001,
        )
        portfolio.apply_fill(sell_fill, OrderSide.SELL)

        # Cash: 10000 - (50000*0.1 + 0) + (51000*0.1 - 5) = 10000 - 5000 + 5095 = 10095
        assert portfolio.cash == pytest.approx(10095.0, rel=1e-6)
        # Position clamped to zero
        assert portfolio.get_position_quantity("BTC/USDT") == pytest.approx(0.0, abs=1e-12)


class TestEnginePositionEntryBoundary:
    """Engine._PositionEntry crosses into Decimal without TypeError on the
    round-trip PnL expression (engine.py L381/L414 — unchanged per 铁律).
    """

    class _AlwaysBuyThenSell:
        """Buy on bar 0, sell on bar 1 — one round-trip trade."""

        def __init__(self) -> None:
            self._bar = 0

        def on_kline(self, kline: KlineEvent):
            from selene_space_types.strategy import Signal

            self._bar += 1
            if self._bar == 1:
                return Signal(
                    strategy_id="test",
                    symbol=kline.symbol,
                    side=OrderSide.BUY,
                    strength=1.0,
                    timestamp=kline.timestamp,
                    reason="buy",
                )
            if self._bar == 2:
                return Signal(
                    strategy_id="test",
                    symbol=kline.symbol,
                    side=OrderSide.SELL,
                    strength=1.0,
                    timestamp=kline.timestamp,
                    reason="sell",
                )
            return None

        def reset(self) -> None:
            self._bar = 0

    def test_round_trip_pnl_no_typeerror(self) -> None:
        """The unchanged L381/L414 PnL expression must not raise TypeError when
        _PositionEntry keeps Decimal (H19 阶段 2C boundary contract).
        """
        config = BacktestConfig(
            initial_cash=100000.0,
            commission=NoCommission(),
            slippage=NoSlippage(),
        )
        engine = BacktestEngine(config=config)

        # Buy at bar1 open=100, sell at bar2 open=110 → round-trip PnL = 10*qty
        klines = [
            KlineEvent(
                exchange="backtest",
                symbol="BTC/USDT",
                timestamp=1711000000000 + i * 3600_000,
                interval="1h",
                open=p,
                high=p * 1.01,
                low=p * 0.99,
                close=p,
                volume=100.0,
                closed=True,
            )
            for i, p in enumerate([100.0, 100.0, 110.0, 110.0])
        ]
        # Should not raise TypeError
        result = engine.run(self._AlwaysBuyThenSell(), klines)

        assert result.trades_count == 1
        trade = result.trades[0]
        # PnL field is present (Decimal — acceptance #9: precision not asserted)
        assert "pnl" in trade
        # Positive PnL (bought at 100, sold at 110)
        assert float(trade["pnl"]) > 0


class TestExecutionSlicingBoundary:
    """twap_slice / vwap_slice operate on Order.quantity (Decimal) without TypeError."""

    def test_twap_slice_no_typeerror(self) -> None:
        order = _order(quantity=100.0)
        children = twap_slice(order, num_slices=5, interval_seconds=60.0)
        assert len(children) == 5
        # Each child's quantity is Decimal (Fill/Order domain)
        for child in children:
            assert isinstance(child.order.quantity, Decimal)
            assert float(child.order.quantity) == pytest.approx(20.0, rel=1e-6)

    def test_vwap_slice_no_typeerror(self) -> None:
        order = _order(quantity=100.0)
        children = vwap_slice(order, volume_profile=[100, 200, 300])
        assert len(children) == 3
        # Sum of slice quantities ≈ 100
        total = sum(float(c.order.quantity) for c in children)
        assert total == pytest.approx(100.0, abs=0.01)
