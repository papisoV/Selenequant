"""Model tests for AccountState / AccountStateSeq (protocol plan §3.1, §5).

Covers:
- JSON serialization of nested Position/Fill/Order/BalanceUpdate Decimal fields → float.
- Empty-domain defaults (balances/positions/orders/trades default to []).
- mode / session_id / seq required (ValidationError when omitted).
- Round-trip of a realistic AccountState through model_dump_json / model_validate_json.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from selene_space_types.trading import (
    AccountState,
    AccountStateSeq,
    BalanceUpdate,
    ExecutionMode,
    Fill,
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)


def _make_position() -> Position:
    return Position(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        pos_side=PositionSide.LONG,
        quantity=Decimal("1.5"),
        quantity_base=Decimal("0.015"),
        avg_entry_price=Decimal("67000.123456"),
        leverage=10,
        margin_mode=MarginMode.CROSS,
        margin=Decimal("100.5"),
        liquidation_price=Decimal("60300.0"),
        unrealized_pnl=Decimal("15.789"),
        unrealized_pnl_pct=Decimal("15.0"),
        realized_pnl=Decimal("0"),
        current_price=Decimal("68000.0"),
        opened_at=1711000000000,
        updated_at=1711000050000,
        accrued_funding=Decimal("0.001"),
    )


def _make_order() -> Order:
    return Order(
        id="ord-001",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        status=OrderStatus.ACCEPTED,
        quantity=Decimal("100.0"),
        price=Decimal("67000.0"),
        filled_quantity=Decimal("0"),
        leverage=10,
        margin_mode=MarginMode.CROSS,
        pos_side=PositionSide.LONG,
        reduce_only=False,
        client_order_id="co-001",
        created_at=1711000000000,
        updated_at=1711000000000,
    )


def _make_fill() -> Fill:
    return Fill(
        id="fill-001",
        order_id="ord-001",
        client_order_id="co-001",
        price=Decimal("67000.0"),
        quantity=Decimal("100.0"),
        fee=Decimal("5.0"),
        fee_currency="USDT",
        fee_rate=Decimal("0.0005"),
        realized_pnl=Decimal("0"),
        exec_type="taker",
        side="buy",
        pos_side="long",
        symbol="BTC-USDT-SWAP",
        leverage=10,
        reduce_only=False,
        timestamp=1711000001000,
    )


def _make_balance() -> BalanceUpdate:
    return BalanceUpdate(
        exchange="okx",
        asset="USDT",
        free=Decimal("5000.25"),
        locked=Decimal("100.5"),
        timestamp=1711000000000,
    )


def _make_account_state() -> AccountState:
    return AccountState(
        session_id="srv-001",
        seq=AccountStateSeq(orders=3, positions=1, balances=2),
        exchange="okx",
        account_id="paper-default",
        mode=ExecutionMode.PAPER,
        balances=[_make_balance()],
        positions=[_make_position()],
        orders=[_make_order()],
        trades=[_make_fill()],
        equity=Decimal("5100.75"),
        timestamp=1711000000000,
    )


class TestJsonSerialization:
    """Nested Decimal fields must serialize to JSON numbers (float), not str."""

    def test_nested_decimals_are_floats(self) -> None:
        state = _make_account_state()
        data = json.loads(state.model_dump_json())

        assert isinstance(data["equity"], float), type(data["equity"])

        pos = data["positions"][0]
        assert isinstance(pos["quantity"], float)
        assert isinstance(pos["avg_entry_price"], float)
        assert isinstance(pos["unrealized_pnl"], float)

        order = data["orders"][0]
        assert isinstance(order["price"], float)
        assert isinstance(order["quantity"], float)

        fill = data["trades"][0]
        assert isinstance(fill["price"], float)
        assert isinstance(fill["fee"], float)

        balance = data["balances"][0]
        assert isinstance(balance["free"], float)
        assert isinstance(balance["locked"], float)

    def test_raw_json_has_no_decimal_or_quoted_number(self) -> None:
        state = _make_account_state()
        raw = state.model_dump_json()
        assert "Decimal" not in raw
        assert '"equity": 5100.75' in raw or '"equity":5100.75' in raw, raw

    def test_seq_serializes_as_object(self) -> None:
        state = _make_account_state()
        data = json.loads(state.model_dump_json())
        assert data["seq"] == {"orders": 3, "positions": 1, "balances": 2}

    def test_model_dump_mode_json_is_float(self) -> None:
        """FastAPI's jsonable_encoder uses model_dump(mode='json') — same contract."""
        data = _make_account_state().model_dump(mode="json")
        assert isinstance(data["equity"], float)
        assert isinstance(data["positions"][0]["quantity"], float)


class TestEmptyDomainDefaults:
    def test_domains_default_to_empty_list(self) -> None:
        state = AccountState(
            session_id="srv-001",
            seq=AccountStateSeq(),
            exchange="okx",
            account_id="paper-default",
            mode=ExecutionMode.PAPER,
            timestamp=1711000000000,
        )
        assert state.balances == []
        assert state.positions == []
        assert state.orders == []
        assert state.trades == []
        assert state.equity is None

    def test_seq_defaults_to_zero_counters(self) -> None:
        seq = AccountStateSeq()
        assert seq.orders == 0
        assert seq.positions == 0
        assert seq.balances == 0


class TestRequiredFields:
    def test_session_id_required(self) -> None:
        with pytest.raises(ValidationError):
            AccountState(
                seq=AccountStateSeq(),
                exchange="okx",
                account_id="paper-default",
                mode=ExecutionMode.PAPER,
                timestamp=1711000000000,
            )

    def test_mode_required(self) -> None:
        with pytest.raises(ValidationError):
            AccountState(
                session_id="srv-001",
                seq=AccountStateSeq(),
                exchange="okx",
                account_id="paper-default",
                timestamp=1711000000000,
            )

    def test_seq_required(self) -> None:
        with pytest.raises(ValidationError):
            AccountState(
                session_id="srv-001",
                exchange="okx",
                account_id="paper-default",
                mode=ExecutionMode.PAPER,
                timestamp=1711000000000,
            )


class TestRoundTrip:
    def test_round_trip_realistic_state(self) -> None:
        state = _make_account_state()
        restored = AccountState.model_validate_json(state.model_dump_json())
        assert restored == state
        # Structural sanity after round trip.
        assert restored.session_id == "srv-001"
        assert restored.seq == AccountStateSeq(orders=3, positions=1, balances=2)
        assert restored.mode == ExecutionMode.PAPER
        assert restored.equity == Decimal("5100.75")
        assert len(restored.positions) == 1
        assert len(restored.orders) == 1
        assert len(restored.trades) == 1
        assert len(restored.balances) == 1
