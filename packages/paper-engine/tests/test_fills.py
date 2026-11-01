"""try_fill gating rules, fee tiering, and enriched fill payloads."""

from __future__ import annotations

import time

import pytest

from selene_space_paper.fills import try_fill
from selene_space_types.trading import Order, OrderSide, OrderStatus, OrderType

PX = 67_000.0
NOTIONAL = 1_000.0


class _ExactPrice:
    """Pass-through slippage so fills land on the feed price exactly."""

    def apply(self, px: float, side: OrderSide, *, order_notional: float = 0.0, avg_24h_volume: float = 0.0) -> float:
        return px


_EXACT = _ExactPrice()


def _order(**overrides) -> Order:
    fields = dict(
        id="ord-test",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.ACCEPTED,
        quantity=NOTIONAL,
        price=None,
        stop_price=None,
        filled_quantity=0.0,
        avg_fill_price=None,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000),
    )
    fields.update(overrides)
    return Order(**fields)


def _fill_at(order: Order, mark: float) -> object:
    got = try_fill(order, mark, slippage_model=_EXACT, latency_range_ms=None)
    assert got is not None
    return got


class TestImmediateExecution:
    @pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL], ids=["buy", "sell"])
    def test_market_order_crosses_the_spread(self, side: OrderSide) -> None:
        fill = _fill_at(_order(side=side), PX)
        assert (float(fill.price), float(fill.quantity)) == (
            pytest.approx(PX),
            pytest.approx(NOTIONAL),
        )


class TestLimitGating:
    def test_buy_limit_waits_for_touch_then_exhausts(self) -> None:
        order = _order(type=OrderType.LIMIT, price=66_000.0)
        assert try_fill(order, PX, slippage_model=_EXACT, latency_range_ms=None) is None
        fill = _fill_at(order, 66_000.0)
        assert float(fill.price) == pytest.approx(66_000.0)
        assert try_fill(order, 65_000.0, slippage_model=_EXACT, latency_range_ms=None) is None

    def test_sell_limit_waits_for_touch_then_exhausts(self) -> None:
        order = _order(type=OrderType.LIMIT, side=OrderSide.SELL, price=68_000.0)
        assert try_fill(order, PX, slippage_model=_EXACT, latency_range_ms=None) is None
        _fill_at(order, 68_000.0)
        assert try_fill(order, 69_000.0, slippage_model=_EXACT, latency_range_ms=None) is None


class TestFillEligibility:
    @pytest.mark.parametrize(
        ("overrides", "why"),
        [
            ({"status": OrderStatus.CANCELLED}, "cancelled"),
            ({"filled_quantity": NOTIONAL}, "already-filled"),
        ],
        ids=["cancelled", "already-filled"],
    )
    def test_ineligible_orders_never_fill(self, overrides: dict, why: str) -> None:
        assert try_fill(_order(**overrides), PX) is None

    def test_partial_book_only_fills_the_remainder(self) -> None:
        order = _order(quantity=2_000.0, filled_quantity=500.0, status=OrderStatus.PARTIAL)
        fill = _fill_at(order, PX)
        assert float(fill.quantity) == pytest.approx(1_500.0)


class TestFeeTiering:
    def test_flat_rate_fee_on_notional(self) -> None:
        fill = try_fill(_order(), PX, fee_rate=0.001)
        assert fill is not None
        assert float(fill.fee) == pytest.approx(NOTIONAL * 0.001)

    @pytest.mark.parametrize(
        ("order_type", "want_exec", "want_rate", "want_fee"),
        [
            (OrderType.MARKET, "taker", 0.0005, 10_000.0 * 0.0005),
            (OrderType.LIMIT, "maker", 0.0002, 10_000.0 * 0.0002),
        ],
        ids=["market-takes", "limit-makes"],
    )
    def test_maker_taker_split_by_order_type(
        self, order_type: OrderType, want_exec: str, want_rate: float, want_fee: float
    ) -> None:
        order = _order(type=order_type, quantity=10_000.0)
        if order_type is OrderType.LIMIT:
            order.price = PX
        fill = try_fill(order, PX, maker_fee_rate=0.0002, taker_fee_rate=0.0005)
        assert fill is not None
        assert (fill.exec_type, float(fill.fee_rate), float(fill.fee)) == (
            want_exec,
            pytest.approx(want_rate),
            pytest.approx(want_fee),
        )

    def test_enriched_payload_fields_exist(self) -> None:
        fill = try_fill(_order(quantity=5_000.0), 65_000.0, maker_fee_rate=0.0002, taker_fee_rate=0.0005)
        assert fill is not None
        for attr in ("fee_rate", "exec_type", "realized_pnl", "symbol", "side", "pos_side"):
            assert hasattr(fill, attr), f"missing {attr}"
