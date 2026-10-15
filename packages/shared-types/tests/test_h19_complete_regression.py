"""H19 Complete Fix regression tests.

Additional regression coverage beyond test_trading.py:
  1. JSON serializer emits float (not str) for all Decimal fields
  2. Cross-field arithmetic stays in Decimal (no float pollution)
  3. Float input via constructor does not pollute precision

Acceptance #4 (0.1+0.2==0.3) is covered in test_trading.py — this file
adds serializer + cross-field regression guards.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from selene_space_types.trading import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)


def _make_order(**overrides) -> Order:
    defaults = dict(
        id="ord-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.ACCEPTED,
        quantity=Decimal("0.1"),
        filled_quantity=Decimal("0"),
        created_at=1,
        updated_at=1,
    )
    defaults.update(overrides)
    return Order(**defaults)


def _make_position(**overrides) -> Position:
    defaults = dict(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        pos_side=PositionSide.LONG,
        quantity=Decimal("1.5"),
        avg_entry_price=Decimal("67000.5"),
        leverage=1,
        margin=Decimal("100"),
        opened_at=1,
        updated_at=1,
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_fill(**overrides) -> Fill:
    defaults = dict(
        id="fill-1",
        order_id="o1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        pos_side=PositionSide.LONG,
        quantity=Decimal("0.5"),
        price=Decimal("67000.123"),
        fee=Decimal("0.075"),
        realized_pnl=Decimal("0"),
        timestamp=1,
    )
    defaults.update(overrides)
    return Fill(**defaults)


class TestH19JSONSerializer:
    """Acceptance #5: JSON serializer emits float (not str) for Decimal fields."""

    def test_order_model_dump_json_emits_float(self) -> None:
        order = _make_order()
        dumped = order.model_dump(mode="json")
        assert isinstance(dumped["quantity"], float), (
            f"quantity should be float in JSON mode, got {type(dumped['quantity']).__name__}"
        )
        assert dumped["quantity"] == pytest.approx(0.1)

    def test_position_model_dump_json_emits_float(self) -> None:
        pos = _make_position()
        dumped = pos.model_dump(mode="json")
        assert isinstance(dumped["quantity"], float)
        assert isinstance(dumped["avg_entry_price"], float)
        assert isinstance(dumped["margin"], float)

    def test_fill_model_dump_json_emits_float(self) -> None:
        fill = _make_fill()
        dumped = fill.model_dump(mode="json")
        assert isinstance(dumped["price"], float)
        assert isinstance(dumped["fee"], float)
        assert isinstance(dumped["realized_pnl"], float)

    def test_order_model_dump_json_roundtrip_preserves_precision(self) -> None:
        """Round-trip via JSON keeps Decimal precision (0.1 stays 0.1, not 0.10000000000000001)."""
        order = _make_order(quantity=Decimal("0.1"))
        raw = order.model_dump_json()
        restored = Order.model_validate_json(raw)
        assert restored.quantity == Decimal("0.1")
        # The float-polluted Decimal(0.1) would not equal Decimal("0.1")
        assert restored.quantity != Decimal(0.1)


class TestH19CrossFieldArithmetic:
    """Cross-field Decimal arithmetic stays precise."""

    def test_order_avg_fill_price_weighted_stays_decimal(self) -> None:
        """Weighted avg price = (p1*q1 + p2*q2) / (q1+q2) stays Decimal."""
        order = _make_order(
            type=OrderType.LIMIT,
            status=OrderStatus.PARTIAL,
            quantity=Decimal("2.0"),
            filled_quantity=Decimal("1.0"),
            avg_fill_price=Decimal("67000"),
        )
        new_fill_qty = Decimal("1.0")
        new_fill_price = Decimal("68000")
        old_total = order.avg_fill_price * order.filled_quantity
        new_total = old_total + new_fill_price * new_fill_qty
        new_filled = order.filled_quantity + new_fill_qty
        new_avg = new_total / new_filled
        assert new_avg == Decimal("67500")

    def test_decimal_no_float_pollution_via_constructor(self) -> None:
        """Constructing Order with float 0.1 yields Decimal('0.1'), not float-polluted."""
        order = _make_order(quantity=0.1, filled_quantity=0.0)
        assert isinstance(order.quantity, Decimal)
        # Decimal(str(0.1)) == Decimal("0.1") — clean, not Decimal(0.1) which is polluted
        assert order.quantity == Decimal("0.1")
        # And arithmetic with it stays clean
        assert order.quantity + Decimal("0.2") == Decimal("0.3")

    def test_position_quantity_plus_quantity_base_decimal(self) -> None:
        """Position.quantity + quantity_base = Decimal 0.3 (would be 0.30000000000000004 in float)."""
        pos = _make_position(quantity=Decimal("0.1"), quantity_base=Decimal("0.2"))
        assert pos.quantity + pos.quantity_base == Decimal("0.3")
        # Float would be 0.30000000000000004
        assert not (0.1 + 0.2 == 0.3)  # sanity: float is imprecise

    def test_fill_price_times_quantity_stays_decimal(self) -> None:
        """Fill.price * quantity = Decimal notional, no float pollution."""
        fill = _make_fill(price=Decimal("0.1"), quantity=Decimal("0.2"))
        notional = fill.price * fill.quantity
        assert notional == Decimal("0.02")
        # Float 0.1 * 0.2 = 0.020000000000000004
        assert not (0.1 * 0.2 == 0.02)
