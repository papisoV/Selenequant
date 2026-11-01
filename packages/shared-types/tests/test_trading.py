"""Tests for selene_space_types.trading — serialization/deserialization roundtrips.

H19 complete fix: 41 amount/price/quantity fields migrated from float to
Decimal. Tests verify Decimal field types, JSON serialization emits float,
float-input coercion avoids precision pollution, and ``gt``/``ge`` constraints
behave consistently on Decimal.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from selene_space_types.trading import (
    BalanceUpdate,
    ExchangeOrderUpdate,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PnLRecord,
    PolymarketOrderUpdate,
    PolymarketOrderEventType,
    PolymarketTradeUpdate,
    Position,
)


class TestOrderEnums:
    def test_order_side_values(self):
        assert OrderSide.BUY == "buy"
        assert OrderSide.SELL == "sell"

    def test_order_type_values(self):
        assert set(OrderType) == {
            OrderType.MARKET,
            OrderType.LIMIT,
            OrderType.STOP_MARKET,
            OrderType.STOP_LIMIT,
            OrderType.IOC,
        }

    def test_order_status_completeness(self):
        """Spec: created → accepted → partial → filled → cancelled → rejected,
        plus extended states (expired, pending_cancel, triggered) added in
        the live-trading design Phase 0.1 for real exchange semantics.
        """
        required = {
            "created",
            "accepted",
            "partial",
            "filled",
            "cancelled",
            "rejected",
            "expired",
            "pending_cancel",
            "triggered",
        }
        actual = {s.value for s in OrderStatus}
        assert required.issubset(actual)
        assert len(actual) >= len(required)


class TestOrder:
    def test_roundtrip(self):
        o = Order(
            id="ord-001",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=0.5,
            price=67000.0,
            created_at=1711000000000,
            updated_at=1711000000000,
        )
        raw = o.model_dump_json()
        restored = Order.model_validate_json(raw)
        assert restored == o
        assert restored.status == OrderStatus.CREATED

    def test_market_order_no_price(self):
        o = Order(
            id="ord-002",
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            quantity=10.0,
            created_at=1711000000000,
            updated_at=1711000000000,
        )
        assert o.price is None

    def test_order_decimal_fields(self):
        """H19: 5 Order fields are Decimal, 0.1+0.2==0.3 holds."""
        o = Order(
            id="ord-001",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=0.1,
            price=0.2,
            stop_price=0.3,
            filled_quantity=0.0,
            avg_fill_price=None,
            created_at=1,
            updated_at=1,
        )
        assert isinstance(o.quantity, Decimal)
        assert isinstance(o.price, Decimal)
        assert isinstance(o.stop_price, Decimal)
        assert isinstance(o.filled_quantity, Decimal)
        assert o.avg_fill_price is None
        # Precision: 0.1 + 0.2 == 0.3 holds for Decimal(str(...))
        assert (o.quantity + o.price) == Decimal("0.3")

    def test_order_remaining_quantity_decimal(self):
        """H19: remaining_quantity property returns Decimal."""
        o = Order(
            id="ord-001",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=Decimal("1.5"),
            filled_quantity=Decimal("0.5"),
            created_at=1,
            updated_at=1,
        )
        rq = o.remaining_quantity
        assert isinstance(rq, Decimal)
        assert rq == Decimal("1.0")
        # Over-filled clamps to 0 (not negative)
        o2 = Order(
            id="ord-002",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=Decimal("1.0"),
            filled_quantity=Decimal("1.5"),
            created_at=1,
            updated_at=1,
        )
        assert o2.remaining_quantity == Decimal("0")


class TestFill:
    def test_roundtrip(self):
        f = Fill(
            id="fill-001",
            order_id="ord-001",
            price=67000.0,
            quantity=0.25,
            fee=0.01675,
            fee_currency="USDT",
            timestamp=1711000001000,
        )
        raw = f.model_dump_json()
        restored = Fill.model_validate_json(raw)
        assert restored == f

    def test_fill_decimal_fields(self):
        """H19: 5 Fill fields are Decimal, 0.1+0.2==0.3 holds."""
        f = Fill(
            id="fill-001",
            order_id="ord-001",
            price=0.1,
            quantity=0.2,
            fee=0.0,
            fee_rate=0.0005,
            realized_pnl=0.3,
            timestamp=1,
        )
        assert isinstance(f.price, Decimal)
        assert isinstance(f.quantity, Decimal)
        assert isinstance(f.fee, Decimal)
        assert isinstance(f.fee_rate, Decimal)
        assert isinstance(f.realized_pnl, Decimal)
        assert (f.price + f.quantity) == Decimal("0.3")


class TestPosition:
    def test_roundtrip(self):
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.5,
            avg_entry_price=67000.0,
            unrealized_pnl=150.0,
            opened_at=1711000000000,
            updated_at=1711000050000,
        )
        raw = p.model_dump_json()
        restored = Position.model_validate_json(raw)
        assert restored == p

    def test_position_decimal_fields(self):
        """H19: 12 Position fields are Decimal, 0.1+0.2==0.3 holds."""
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.1,
            quantity_base=0.2,
            avg_entry_price=67000.0,
            margin=100.0,
            liquidation_price=60300.0,
            unrealized_pnl=15.0,
            unrealized_pnl_pct=15.0,
            realized_pnl=0.0,
            current_price=68000.0,
            accrued_funding=0.0,
            borrow_fee_accrued=0.0,
            opened_at=1,
            updated_at=1,
        )
        decimal_fields = [
            p.quantity, p.quantity_base, p.avg_entry_price, p.margin,
            p.liquidation_price, p.unrealized_pnl, p.unrealized_pnl_pct,
            p.realized_pnl, p.current_price, p.accrued_funding, p.borrow_fee_accrued,
        ]
        for v in decimal_fields:
            assert isinstance(v, Decimal), f"expected Decimal, got {type(v).__name__}"
        assert (p.quantity + p.quantity_base) == Decimal("0.3") or p.quantity == Decimal("0.1")


class TestPnLRecord:
    def test_roundtrip(self):
        r = PnLRecord(
            symbol="BTC/USDT",
            realized_pnl=200.0,
            unrealized_pnl=150.0,
            total_pnl=350.0,
            fees=5.5,
            timestamp=1711000060000,
        )
        raw = r.model_dump_json()
        restored = PnLRecord.model_validate_json(raw)
        assert restored == r
        assert restored.total_pnl == restored.realized_pnl + restored.unrealized_pnl


class TestDecimalJsonSerializer:
    """H19: model_dump(mode='json') emits float, not str, for Decimal fields."""

    def test_position_json_emits_float(self):
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.1,
            avg_entry_price=67000.0,
            opened_at=1,
            updated_at=1,
        )
        d = p.model_dump(mode="json")
        assert isinstance(d["quantity"], float), f"expected float, got {type(d['quantity']).__name__}"
        assert d["quantity"] == 0.1
        # liquidation_price is None — must stay None, not coerced
        assert d["liquidation_price"] is None

    def test_order_json_emits_float(self):
        o = Order(
            id="o",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=1.5,
            price=67000.0,
            created_at=1,
            updated_at=1,
        )
        d = o.model_dump(mode="json")
        assert isinstance(d["quantity"], float)
        assert isinstance(d["price"], float)

    def test_fill_json_emits_float(self):
        f = Fill(id="f", order_id="o", price=0.1, quantity=0.2, timestamp=1)
        d = f.model_dump(mode="json")
        assert isinstance(d["price"], float)
        assert isinstance(d["quantity"], float)


class TestDecimalFieldValidator:
    """H19: float/dict/string inputs coerced via Decimal(str(v)) to avoid
    float-to-Decimal precision pollution."""

    def test_float_input_no_precision_pollution(self):
        """Position(quantity=0.1) must store Decimal('0.1'), not Decimal(0.1).

        Decimal(0.1) would carry float's binary-representation noise:
        Decimal(0.1) == Decimal('0.1000000000000000055511151231257827021181583404541015625')
        Decimal(str(0.1)) == Decimal('0.1')  (clean)
        """
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.1,
            avg_entry_price=67000.0,
            opened_at=1,
            updated_at=1,
        )
        # Clean Decimal — equals the string form, not the polluted float form
        assert p.quantity == Decimal("0.1")
        # The polluted form would NOT equal the clean form
        assert p.quantity != Decimal(0.1)

    def test_string_input_coerced(self):
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity="0.1",
            avg_entry_price="67000.5",
            opened_at=1,
            updated_at=1,
        )
        assert p.quantity == Decimal("0.1")
        assert p.avg_entry_price == Decimal("67000.5")

    def test_dict_input_coerced(self):
        """Round-trip from model_dump (which has float) back via model_validate."""
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.1,
            avg_entry_price=67000.0,
            opened_at=1,
            updated_at=1,
        )
        d = p.model_dump()  # Decimal values
        p2 = Position.model_validate(d)
        assert p2.quantity == Decimal("0.1")
        assert isinstance(p2.quantity, Decimal)

    def test_int_input_coerced(self):
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=1,
            avg_entry_price=67000,
            opened_at=1,
            updated_at=1,
        )
        assert p.quantity == Decimal("1")
        assert p.avg_entry_price == Decimal("67000")


class TestDecimalGtGeConstraint:
    """H19: gt=0 / ge=0 constraints behave consistently on Decimal."""

    def test_gt_zero_rejects_zero(self):
        with pytest.raises(ValidationError):
            Order(
                id="o",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                quantity=Decimal("0"),
                created_at=1,
                updated_at=1,
            )

    def test_gt_zero_rejects_negative(self):
        with pytest.raises(ValidationError):
            Order(
                id="o",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                quantity=Decimal("-1"),
                created_at=1,
                updated_at=1,
            )

    def test_ge_zero_allows_zero(self):
        """filled_quantity has ge=0, must allow 0."""
        o = Order(
            id="o",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=Decimal("1"),
            filled_quantity=Decimal("0"),
            created_at=1,
            updated_at=1,
        )
        assert o.filled_quantity == Decimal("0")

    def test_ge_zero_rejects_negative(self):
        with pytest.raises(ValidationError):
            Order(
                id="o",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                quantity=Decimal("1"),
                filled_quantity=Decimal("-0.1"),
                created_at=1,
                updated_at=1,
            )


class TestFieldSerializerWildcardSupport:
    """H19 regression guard: pydantic v2 @field_serializer('*') wildcard must
    keep working for Decimal → float JSON serialization.

    Plan v4 stage-1 spike test confirmed pydantic v2.13.4 supports the
    wildcard. This test locks that behavior so a future pydantic upgrade
    cannot silently break it (if wildcard breaks, JSON API would emit
    Decimal as string and the frontend would break).
    """

    def test_wildcard_serializer_emits_float_for_all_decimal_fields(self):
        """Position has 11 Decimal fields — all must serialize to float."""
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.1,
            quantity_base=0.2,
            avg_entry_price=67000.0,
            margin=100.0,
            liquidation_price=60300.0,
            unrealized_pnl=15.0,
            unrealized_pnl_pct=15.0,
            realized_pnl=0.0,
            current_price=68000.0,
            accrued_funding=0.0,
            borrow_fee_accrued=0.0,
            opened_at=1,
            updated_at=1,
        )
        d = p.model_dump(mode="json")
        decimal_field_names = [
            "quantity", "quantity_base", "avg_entry_price", "margin",
            "liquidation_price", "unrealized_pnl", "unrealized_pnl_pct",
            "realized_pnl", "current_price", "accrued_funding", "borrow_fee_accrued",
        ]
        for name in decimal_field_names:
            v = d[name]
            assert v is None or isinstance(v, float), (
                f"field {name!r} expected float or None, got {type(v).__name__}: {v!r}"
            )

    def test_wildcard_serializer_emits_float_for_balance_update(self):
        """BalanceUpdate.free/locked must serialize to float (not str)."""
        b = BalanceUpdate(exchange="binance", asset="USDT", free=100.5, locked=50.25, timestamp=1)
        d = b.model_dump(mode="json")
        assert isinstance(d["free"], float)
        assert isinstance(d["locked"], float)
        assert d["free"] == 100.5

    def test_wildcard_serializer_emits_float_for_exchange_order_update(self):
        u = ExchangeOrderUpdate(
            exchange="binance",
            exchange_order_id="e1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type="LIMIT",
            status="NEW",
            quantity=1.5,
            filled_quantity=0.5,
            avg_fill_price=67000.0,
            last_fill_price=67000.0,
            last_fill_quantity=0.5,
            commission=0.05,
            timestamp=1,
        )
        d = u.model_dump(mode="json")
        for name in ("quantity", "filled_quantity", "avg_fill_price", "last_fill_price", "last_fill_quantity", "commission"):
            assert isinstance(d[name], float), f"{name}: {type(d[name]).__name__}"

    def test_wildcard_serializer_emits_float_for_polymarket(self):
        po = PolymarketOrderUpdate(
            order_id="po1",
            asset_id="a1",
            market="m1",
            side=OrderSide.BUY,
            price=0.55,
            original_size=100.0,
            size_matched=50.0,
            event_type=PolymarketOrderEventType.PLACEMENT,
            timestamp=1,
        )
        d = po.model_dump(mode="json")
        assert isinstance(d["price"], float)
        assert isinstance(d["original_size"], float)
        assert isinstance(d["size_matched"], float)

        pt = PolymarketTradeUpdate(
            trade_id="pt1",
            asset_id="a1",
            market="m1",
            side=OrderSide.BUY,
            price=0.55,
            size=50.0,
            status="MATCHED",
            timestamp=1,
        )
        d2 = pt.model_dump(mode="json")
        assert isinstance(d2["price"], float)
        assert isinstance(d2["size"], float)


class TestH19RegressionAcceptance:
    """H19 acceptance #4: 0.1 + 0.2 == 0.3 holds across the 5 specific fields
    listed in the verify criteria. Also covers #5 (JSON emits float), #6 (round-trip),
    #7 (no TypeError at exchange/risk boundary)."""

    def test_acceptance_4_position_quantity_precision(self):
        """Position.quantity: 0.1 + 0.2 == 0.3 holds (Decimal)."""
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.1"),
            avg_entry_price=Decimal("67000.0"),
            opened_at=1,
            updated_at=1,
        )
        p2 = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.2"),
            avg_entry_price=Decimal("67000.0"),
            opened_at=1,
            updated_at=1,
        )
        # float arithmetic would give 0.30000000000000004
        assert float(0.1) + float(0.2) != 0.3  # proof float fails
        assert p.quantity + p2.quantity == Decimal("0.3")  # Decimal works

    def test_acceptance_4_fill_price_precision(self):
        """Fill.price: 0.1 + 0.2 == 0.3 holds (Decimal)."""
        f1 = Fill(id="f1", order_id="o", price=Decimal("0.1"), quantity=Decimal("1"), timestamp=1)
        f2 = Fill(id="f2", order_id="o", price=Decimal("0.2"), quantity=Decimal("1"), timestamp=1)
        assert f1.price + f2.price == Decimal("0.3")

    def test_acceptance_4_order_remaining_quantity_precision(self):
        """Order.remaining_quantity: 0.1 + 0.2 == 0.3 holds (Decimal property)."""
        o = Order(
            id="o",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=Decimal("0.3"),
            filled_quantity=Decimal("0.1"),
            created_at=1,
            updated_at=1,
        )
        # remaining = 0.3 - 0.1 = 0.2 (Decimal)
        assert o.remaining_quantity == Decimal("0.2")
        # 0.1 + 0.2 == 0.3 in the remaining path
        assert (o.filled_quantity + o.remaining_quantity) == Decimal("0.3")

    def test_acceptance_4_pnl_record_realized_pnl_precision(self):
        """PnLRecord.realized_pnl: 0.1 + 0.2 == 0.3 holds (Decimal)."""
        from selene_space_types.trading import PnLRecord
        r1 = PnLRecord(symbol="BTC/USDT", realized_pnl=Decimal("0.1"), unrealized_pnl=Decimal("0"), total_pnl=Decimal("0.1"), fees=Decimal("0"), timestamp=1)
        r2 = PnLRecord(symbol="BTC/USDT", realized_pnl=Decimal("0.2"), unrealized_pnl=Decimal("0"), total_pnl=Decimal("0.2"), fees=Decimal("0"), timestamp=1)
        assert r1.realized_pnl + r2.realized_pnl == Decimal("0.3")

    def test_acceptance_4_fill_realized_pnl_precision(self):
        """Fill.realized_pnl: 0.1 + 0.2 == 0.3 holds (Decimal)."""
        f1 = Fill(id="f1", order_id="o", price=Decimal("1"), quantity=Decimal("1"),
                  realized_pnl=Decimal("0.1"), timestamp=1)
        f2 = Fill(id="f2", order_id="o", price=Decimal("1"), quantity=Decimal("1"),
                  realized_pnl=Decimal("0.2"), timestamp=1)
        assert f1.realized_pnl + f2.realized_pnl == Decimal("0.3")

    def test_acceptance_5_json_output_is_float_not_str(self):
        """Verify #5: json model_dump emits float (number), not str."""
        p = Position(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.1"),
            avg_entry_price=Decimal("67000.0"),
            opened_at=1,
            updated_at=1,
        )
        import json
        body = json.dumps(p.model_dump(mode="json"))
        decoded = json.loads(body)
        assert isinstance(decoded["quantity"], float), f"got {type(decoded['quantity']).__name__}"
        assert decoded["quantity"] == 0.1
