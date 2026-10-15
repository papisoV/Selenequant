"""Phase 4 tests — execution layer: partial fills, bid-ask spread, TWAP/VWAP."""

from __future__ import annotations

import pytest

from selene_space_backtest.broker import SimulatedBroker
from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.execution import twap_slice, vwap_slice
from selene_space_backtest.slippage import (
    BidAskSpreadModel,
    CompositeSlippage,
    FixedSlippage,
    NoSlippage,
)
from selene_space_types.market import KlineEvent
from selene_space_types.trading import Order, OrderSide, OrderStatus, OrderType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kline(
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
    ts: int = 1711000000000,
) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="1h",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


def _order(
    qty: float = 100.0,
    side: OrderSide = OrderSide.BUY,
    otype: OrderType = OrderType.MARKET,
    price: float | None = None,
    oid: str = "ord-1",
) -> Order:
    return Order(
        id=oid,
        symbol="BTC/USDT",
        side=side,
        type=otype,
        quantity=qty,
        price=price,
        created_at=1711000000000,
        updated_at=1711000000000,
    )


# ===========================================================================
# Partial Fill Tests
# ===========================================================================


class TestPartialFill:
    """Partial fill simulation in SimulatedBroker."""

    def test_full_fill_by_default(self) -> None:
        """Default broker fills entire order in one shot."""
        broker = SimulatedBroker()
        order = _order(qty=100.0)
        kline = _kline()
        fill = broker.execute(order, kline)

        assert fill is not None
        assert fill.quantity == 100.0
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 100.0

    def test_partial_fill_rate(self) -> None:
        """partial_fill_rate=0.5 fills half the remaining qty per bar."""
        broker = SimulatedBroker(partial_fill_rate=0.5)
        order = _order(qty=100.0)
        kline = _kline()

        # First bar: fill 50% of 100 = 50
        fill1 = broker.execute(order, kline)
        assert fill1 is not None
        assert fill1.quantity == pytest.approx(50.0)
        assert order.status == OrderStatus.PARTIAL
        assert order.filled_quantity == pytest.approx(50.0)

        # Second bar: fill 50% of remaining 50 = 25
        fill2 = broker.execute(order, _kline(ts=1711003600000))
        assert fill2 is not None
        assert fill2.quantity == pytest.approx(25.0)
        assert order.status == OrderStatus.PARTIAL
        assert order.filled_quantity == pytest.approx(75.0)

    def test_partial_fill_completes(self) -> None:
        """Partial fills eventually complete the full order."""
        broker = SimulatedBroker(partial_fill_rate=0.5)
        order = _order(qty=80.0)
        total_filled = 0.0

        for i in range(50):  # Enough iterations for convergence
            fill = broker.execute(order, _kline(ts=1711000000000 + i * 3600000))
            if fill is None:
                break
            # fill.quantity is Decimal (H19) — coerce at boundary.
            total_filled += float(fill.quantity)
            if order.status == OrderStatus.FILLED:
                break

        # Due to halving, order may never reach exactly 80.0
        # but should be very close. Check that filled_quantity ≈ quantity
        # and status is FILLED once remaining is negligible
        assert total_filled == pytest.approx(80.0, abs=0.5)

    def test_volume_participation_rate(self) -> None:
        """volume_participation_rate caps fill at fraction of bar volume."""
        # Bar volume = 1000, participation = 0.1 → max fill = 100
        broker = SimulatedBroker(volume_participation_rate=0.1)
        order = _order(qty=500.0)  # Want 500 but only 100 available
        kline = _kline(volume=1000.0)

        fill = broker.execute(order, kline)
        assert fill is not None
        assert fill.quantity == pytest.approx(100.0)
        assert order.status == OrderStatus.PARTIAL

    def test_volume_participation_with_small_remaining(self) -> None:
        """If remaining qty < volume cap, fill the remaining amount."""
        broker = SimulatedBroker(volume_participation_rate=0.5)
        order = _order(qty=50.0)  # Only want 50, volume allows 500
        kline = _kline(volume=1000.0)

        fill = broker.execute(order, kline)
        assert fill is not None
        assert fill.quantity == pytest.approx(50.0)
        assert order.status == OrderStatus.FILLED

    def test_vwap_with_partial_fills(self) -> None:
        """VWAP avg price updates correctly across partial fills."""
        broker = SimulatedBroker(partial_fill_rate=0.5, commission=NoCommission())
        order = _order(qty=100.0)

        # Bar 1: open=100, fill 50 at 100
        fill1 = broker.execute(order, _kline(open_=100.0))
        assert fill1 is not None
        assert order.avg_fill_price == pytest.approx(100.0)

        # Bar 2: open=110, fill 25 at 110
        fill2 = broker.execute(order, _kline(open_=110.0, ts=1711003600000))
        assert fill2 is not None
        # VWAP = (50*100 + 25*110) / 75 = 7750/75 = 103.33
        assert order.avg_fill_price == pytest.approx(103.333, abs=0.01)

    def test_limit_order_partial_fill(self) -> None:
        """Limit orders also support partial fills."""
        broker = SimulatedBroker(partial_fill_rate=0.5)
        order = _order(qty=100.0, otype=OrderType.LIMIT, price=95.0)
        # kline low=90, so buy limit at 95 is triggered
        kline = _kline(low=90.0)

        fill = broker.execute(order, kline)
        assert fill is not None
        assert fill.quantity == pytest.approx(50.0)
        assert order.status == OrderStatus.PARTIAL

    def test_invalid_partial_fill_rate(self) -> None:
        """partial_fill_rate must be in (0, 1]."""
        with pytest.raises(ValueError):
            SimulatedBroker(partial_fill_rate=0.0)
        with pytest.raises(ValueError):
            SimulatedBroker(partial_fill_rate=1.5)

    def test_invalid_volume_participation_rate(self) -> None:
        """volume_participation_rate must be in [0, 1]."""
        with pytest.raises(ValueError):
            SimulatedBroker(volume_participation_rate=-0.1)
        with pytest.raises(ValueError):
            SimulatedBroker(volume_participation_rate=1.5)


# ===========================================================================
# Bid-Ask Spread Model Tests
# ===========================================================================


class TestBidAskSpreadModel:
    """Bid-ask spread cost model."""

    def test_default_spread(self) -> None:
        """Default spread applies 5bps half-spread."""
        model = BidAskSpreadModel(default_spread_bps=5.0)
        # Buy: price goes up by 5bps
        buy_price = model.apply(100.0, OrderSide.BUY)
        assert buy_price == pytest.approx(100.0 * 1.0005)
        # Sell: price goes down by 5bps
        sell_price = model.apply(100.0, OrderSide.SELL)
        assert sell_price == pytest.approx(100.0 * 0.9995)

    def test_per_symbol_spread(self) -> None:
        """Per-symbol spread overrides default."""
        model = BidAskSpreadModel(
            default_spread_bps=5.0,
            symbol_spread_bps={"BTC-USDT": 1.5, "DOGE-USDT": 20.0},
        )
        # BTC gets 1.5bps
        btc_buy = model.apply(50000.0, OrderSide.BUY, symbol="BTC-USDT")
        assert btc_buy == pytest.approx(50000.0 * 1.00015)

        # DOGE gets 20bps
        doge_sell = model.apply(0.1, OrderSide.SELL, symbol="DOGE-USDT")
        assert doge_sell == pytest.approx(0.1 * 0.998)

        # Unknown symbol gets default 5bps
        other_buy = model.apply(100.0, OrderSide.BUY, symbol="ETH-USDT")
        assert other_buy == pytest.approx(100.0 * 1.0005)

    def test_apply_with_actual_bid_ask(self) -> None:
        """When actual bid/ask data is available, use it directly."""
        model = BidAskSpreadModel(default_spread_bps=5.0)
        # Buy uses ask price
        buy_price = model.apply_with_spread_data(100.0, OrderSide.BUY, bid=99.9, ask=100.1)
        assert buy_price == 100.1

        # Sell uses bid price
        sell_price = model.apply_with_spread_data(100.0, OrderSide.SELL, bid=99.9, ask=100.1)
        assert sell_price == 99.9

    def test_fallback_when_no_bid_ask(self) -> None:
        """Falls back to bps-based calculation when bid/ask not provided."""
        model = BidAskSpreadModel(default_spread_bps=5.0)
        price = model.apply_with_spread_data(100.0, OrderSide.BUY)
        assert price == pytest.approx(100.0 * 1.0005)

    def test_negative_spread_rejected(self) -> None:
        """Negative spread bps is rejected."""
        with pytest.raises(ValueError):
            BidAskSpreadModel(default_spread_bps=-1.0)

    def test_tier_presets(self) -> None:
        """Tier presets are reasonable values."""
        assert BidAskSpreadModel.TIER1_SPREAD_BPS < BidAskSpreadModel.TIER2_SPREAD_BPS
        assert BidAskSpreadModel.TIER2_SPREAD_BPS < BidAskSpreadModel.TIER3_SPREAD_BPS


class TestCompositeSlippage:
    """Composite slippage = market impact + bid-ask spread."""

    def test_composite_adds_both_costs(self) -> None:
        """Composite applies slippage then spread."""
        slippage = FixedSlippage(bps=1.0)  # 1bp market impact
        spread = BidAskSpreadModel(default_spread_bps=5.0)  # 5bps spread
        composite = CompositeSlippage(slippage, spread)

        # Buy: first slippage (+1bp), then spread (+5bp)
        # 100 * 1.0001 = 100.01, then 100.01 * 1.0005 = 100.060005
        price = composite.apply(100.0, OrderSide.BUY, symbol="BTC/USDT")
        assert price == pytest.approx(100.0 * 1.0001 * 1.0005, abs=0.001)

    def test_composite_with_no_slippage(self) -> None:
        """NoSlippage + spread = just spread cost."""
        composite = CompositeSlippage(NoSlippage(), BidAskSpreadModel(default_spread_bps=3.0))
        price = composite.apply(100.0, OrderSide.BUY, symbol="ETH/USDT")
        assert price == pytest.approx(100.0 * 1.0003)


# ===========================================================================
# TWAP/VWAP Execution Tests
# ===========================================================================


class TestTWAPSlicing:
    """TWAP order slicing."""

    def test_equal_slices(self) -> None:
        """TWAP splits order into equal-size slices."""
        order = _order(qty=100.0)
        children = twap_slice(order, num_slices=5, interval_seconds=60.0)

        assert len(children) == 5
        for child in children:
            assert child.order.quantity == pytest.approx(20.0)
            assert child.total_slices == 5

    def test_scheduled_times(self) -> None:
        """Slices are scheduled at correct intervals."""
        order = _order(qty=50.0)
        children = twap_slice(order, num_slices=3, interval_seconds=120.0)

        assert children[0].scheduled_time == order.created_at
        assert children[1].scheduled_time == order.created_at + 120_000
        assert children[2].scheduled_time == order.created_at + 240_000

    def test_single_slice(self) -> None:
        """num_slices=1 returns the full order as one child."""
        order = _order(qty=100.0)
        children = twap_slice(order, num_slices=1)

        assert len(children) == 1
        assert children[0].order.quantity == pytest.approx(100.0)

    def test_zero_quantity_returns_empty(self) -> None:
        """Zero quantity order returns no children."""
        # Order model requires qty > 0, so test twap_slice guard directly
        order = _order(qty=1.0)
        order.quantity = 0.0  # Bypass validator for edge case test
        children = twap_slice(order, num_slices=5)
        assert children == []

    def test_invalid_num_slices(self) -> None:
        """num_slices < 1 raises ValueError."""
        order = _order(qty=100.0)
        with pytest.raises(ValueError):
            twap_slice(order, num_slices=0)


class TestVWAPSlicing:
    """VWAP order slicing."""

    def test_volume_weighted_quantities(self) -> None:
        """VWAP slices are proportional to volume profile."""
        order = _order(qty=100.0)
        # Profile: [100, 200, 300] → weights [1/6, 2/6, 3/6]
        children = vwap_slice(order, volume_profile=[100, 200, 300])

        assert len(children) == 3
        # order.quantity is Decimal (H19) — compare in float domain.
        assert float(children[0].order.quantity) == pytest.approx(100.0 / 6, abs=0.01)
        assert float(children[1].order.quantity) == pytest.approx(200.0 / 6, abs=0.01)
        # Last slice gets remainder
        total = sum(float(c.order.quantity) for c in children)
        assert total == pytest.approx(100.0, abs=0.01)

    def test_last_slice_gets_remainder(self) -> None:
        """Last slice absorbs rounding drift."""
        order = _order(qty=100.0)
        children = vwap_slice(order, volume_profile=[1, 1, 1])

        # First two: 100 * (1/3) each, last: remainder
        assert float(children[0].order.quantity) == pytest.approx(100.0 / 3, abs=0.01)
        assert float(children[1].order.quantity) == pytest.approx(100.0 / 3, abs=0.01)
        total = sum(float(c.order.quantity) for c in children)
        assert total == pytest.approx(100.0, abs=0.001)

    def test_empty_profile_rejected(self) -> None:
        """Empty volume profile raises ValueError."""
        order = _order(qty=100.0)
        with pytest.raises(ValueError):
            vwap_slice(order, volume_profile=[])

    def test_zero_total_volume_rejected(self) -> None:
        """All-zero volume profile raises ValueError."""
        order = _order(qty=100.0)
        with pytest.raises(ValueError):
            vwap_slice(order, volume_profile=[0, 0, 0])


# ===========================================================================
# Integration: Spread + Broker
# ===========================================================================


class TestSpreadWithBroker:
    """Bid-ask spread integrated with SimulatedBroker."""

    def test_spread_increases_fill_cost(self) -> None:
        """Broker with spread model charges more than without."""
        kline = _kline(open_=100.0)
        order = _order(qty=100.0)

        # Without spread
        broker_no_spread = SimulatedBroker(slippage=NoSlippage(), commission=NoCommission())
        fill_plain = broker_no_spread.execute(order, kline)
        assert fill_plain is not None
        plain_price = fill_plain.price

        # With spread (reset order)
        order2 = _order(qty=100.0, oid="ord-2")
        spread = BidAskSpreadModel(default_spread_bps=5.0)
        composite = CompositeSlippage(NoSlippage(), spread)
        broker_with_spread = SimulatedBroker(slippage=composite, commission=NoCommission())
        fill_spread = broker_with_spread.execute(order2, kline)
        assert fill_spread is not None

        # Buy with spread should be more expensive
        assert fill_spread.price > plain_price
