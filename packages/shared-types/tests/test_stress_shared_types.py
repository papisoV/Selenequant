"""Boundary-condition stress tests for shared-types (cleanroom hardening batch).

Covers: empty/extreme field values, Decimal coercion precision, JSON
serialization round-trips with malformed inputs, and validation rejections.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from selene_space_types.common import APIResponse, ErrorInfo, Pagination
from selene_space_types.events import DiagnosticEvent, DiagnosticLevel, HookEvent
from selene_space_types.trading import Order, OrderSide, OrderType


def _base_order_kwargs() -> dict:
    return dict(
        id="ord-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        created_at=1711000000000,
        updated_at=1711000000000,
    )


# ---------------------------------------------------------------------------
# Order: Decimal coercion at the boundary
# ---------------------------------------------------------------------------


class TestOrderDecimalCoercion:
    def test_float_input_uses_str_coercion_no_binary_artifacts(self) -> None:
        order = Order(**_base_order_kwargs(), quantity=0.1, price=67000.5)
        assert order.quantity == Decimal("0.1")
        assert order.price == Decimal("67000.5")

    def test_extreme_precision_string_preserved(self) -> None:
        raw = "12345.67890123456789012345678901234567890"
        order = Order(**_base_order_kwargs(), quantity=raw, price="99999.99")
        assert order.quantity == Decimal(raw)

    def test_bool_input_coerces_to_zero_or_one(self) -> None:
        order = Order(**_base_order_kwargs(), quantity=True, price=False)
        assert order.quantity == Decimal(1)
        assert order.price == Decimal(0)

    def test_none_price_allowed_for_market_order(self) -> None:
        order = Order(
            **(_base_order_kwargs() | {"type": OrderType.MARKET}), quantity="1"
        )
        assert order.price is None

    def test_json_serialization_emits_float(self) -> None:
        order = Order(**_base_order_kwargs(), quantity="10.25", price="67000")
        payload = json.loads(order.model_dump_json())
        assert isinstance(payload["quantity"], float)
        assert payload["quantity"] == 10.25
        assert isinstance(payload["price"], float)

    def test_json_round_trip_preserves_value(self) -> None:
        order = Order(**_base_order_kwargs(), quantity="0.1", price="0.2")
        revived = Order.model_validate_json(order.model_dump_json())
        assert revived.quantity == order.quantity
        assert revived.price == order.price

    def test_remaining_quantity_clamped_at_zero(self) -> None:
        order = Order(**_base_order_kwargs(), quantity="10", price="1")
        object.__setattr__(order, "filled_quantity", Decimal("15"))
        assert order.remaining_quantity == Decimal("0")


class TestOrderValidationRejections:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("quantity", "0"),
            ("quantity", "-1"),
            ("price", "-0.01"),
            ("stop_price", "-100"),
            ("filled_quantity", "-0.001"),
        ],
    )
    def test_nonpositive_amounts_rejected(self, field: str, value: str) -> None:
        kwargs = _base_order_kwargs() | {"type": OrderType.MARKET}
        kwargs[field] = value
        with pytest.raises(ValidationError):
            Order(**kwargs)

    def test_leverage_above_cap_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Order(**_base_order_kwargs(), quantity="1", leverage=126)

    def test_leverage_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Order(**_base_order_kwargs(), quantity="1", leverage=0)

    def test_missing_required_identity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Order(symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                  created_at=1, updated_at=1, quantity="1")

    def test_wrong_type_side_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Order(**(_base_order_kwargs() | {"side": "sideways"}), quantity="1")


# ---------------------------------------------------------------------------
# Events: empty / extreme payloads
# ---------------------------------------------------------------------------


class TestDiagnosticEventEdges:
    def test_all_empty_strings_accepted(self) -> None:
        event = DiagnosticEvent(
            sequence_id=0, timestamp=0, category="", level="debug", message=""
        )
        assert event.level is DiagnosticLevel.DEBUG

    def test_huge_int_fields_accepted(self) -> None:
        big = 2**63 - 1
        event = DiagnosticEvent(
            sequence_id=big, timestamp=big, category="x",
            level="critical", message="x",
        )
        assert event.sequence_id == big

    def test_bogus_level_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiagnosticEvent(
                sequence_id=1, timestamp=1, category="x",
                level="bogus", message="x",
            )

    def test_data_dict_default_is_none_and_payload_round_trips(self) -> None:
        event = DiagnosticEvent(
            sequence_id=1, timestamp=1, category="x", level="info",
            message="m", data={"k": [1, 2]},
        )
        revived = DiagnosticEvent.model_validate_json(event.model_dump_json())
        assert revived.data == {"k": [1, 2]}

    def test_hook_event_payload_defaults_to_fresh_empty_dict(self) -> None:
        a = HookEvent(sequence_id=1, timestamp=1, hook_name="h")
        b = HookEvent(sequence_id=2, timestamp=2, hook_name="h")
        a.payload["injected"] = True
        assert b.payload == {}


# ---------------------------------------------------------------------------
# Envelope: generic response round-trips with malformed input
# ---------------------------------------------------------------------------


class TestAPIResponseEnvelope:
    def test_success_shape(self) -> None:
        resp = APIResponse[dict](data={"a": 1})
        assert resp.error is None
        assert resp.data == {"a": 1}

    def test_failure_shape(self) -> None:
        err = ErrorInfo(code="E1", message="bad", details=None)
        resp = APIResponse[dict](data=None, error=err)
        assert resp.data is None
        assert resp.error.code == "E1"

    def test_error_missing_code_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ErrorInfo(message="no code")

    def test_malformed_json_roundtrip(self) -> None:
        resp = APIResponse[int](data=42)
        raw = resp.model_dump_json()
        truncated = raw[: len(raw) // 2]
        with pytest.raises(ValidationError):
            APIResponse[int].model_validate_json(truncated)

    def test_pagination_extremes_accepted(self) -> None:
        page = Pagination(offset=-1, limit=-1, total=0)
        assert page.offset == -1  # model has no ge constraint; lock current shape
