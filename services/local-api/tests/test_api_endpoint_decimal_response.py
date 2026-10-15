"""H19 stage 3A: verify /api/v1/trading/positions returns float (not Decimal/str).

Stage 1 added ``@field_serializer("*", when_used="json")`` on every Decimal
field in shared-types, so FastAPI's ``jsonable_encoder`` (which calls
``model_dump(mode="json")``) should emit floats. This test guards against
regressions where a Decimal leaks through to the HTTP response body.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.trading import router as trading_router
from selene_space_types.trading import (
    MarginMode,
    OrderSide,
    Position,
    PositionSide,
)


class _FakeEngine:
    """Returns a single Position with Decimal fields for serialization check."""

    def __init__(self, position: Position | None = None) -> None:
        self._position = position

    async def get_positions(self, account_id: str) -> list[Position]:
        return [self._position] if self._position is not None else []


def _make_app(engine: _FakeEngine) -> FastAPI:
    app = FastAPI()
    app.include_router(trading_router, prefix="/api/v1")

    from app.core import dependencies as deps

    deps._live_engine = engine
    deps._execution_mode = "paper"
    return app


def _cleanup_deps() -> None:
    from app.core import dependencies as deps

    deps._live_engine = None
    deps._execution_mode = "paper"


@pytest.fixture(autouse=True)
def _restore_deps():
    yield
    _cleanup_deps()


def _make_decimal_position() -> Position:
    """Build a Position with Decimal values that would leak precision if not serialized."""
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


class TestPositionsEndpointDecimalResponse:
    """Verify /api/v1/trading/positions emits float in HTTP response body."""

    def test_positions_quantity_is_float_not_str(self) -> None:
        """Response body `quantity` must be float, not Decimal-as-str."""
        pos = _make_decimal_position()
        engine = _FakeEngine(position=pos)
        app = _make_app(engine)
        client = TestClient(app)

        resp = client.get("/api/v1/trading/positions")
        assert resp.status_code == 200

        # Parse the raw HTTP body — this is what the frontend receives.
        body = json.loads(resp.content)
        assert isinstance(body, list)
        assert len(body) == 1

        entry = body[0]
        # Core assertion: quantity is a JSON number (float), not a string.
        assert isinstance(entry["quantity"], float), (
            f"Expected float, got {type(entry['quantity']).__name__}: {entry['quantity']!r}"
        )
        assert entry["quantity"] == pytest.approx(1.5, rel=1e-9)

        # Other Decimal fields must also be float, not str.
        for field in (
            "quantity_base",
            "avg_entry_price",
            "margin",
            "liquidation_price",
            "unrealized_pnl",
            "unrealized_pnl_pct",
            "realized_pnl",
            "current_price",
            "accrued_funding",
        ):
            value = entry.get(field)
            if value is None:
                continue
            assert isinstance(value, float), (
                f"Field {field!r} expected float, got {type(value).__name__}: {value!r}"
            )

    def test_positions_raw_body_has_no_decimal_string(self) -> None:
        """Raw HTTP body must not contain 'Decimal(' — confirms no Decimal-as-str leak."""
        pos = _make_decimal_position()
        engine = _FakeEngine(position=pos)
        app = _make_app(engine)
        client = TestClient(app)

        resp = client.get("/api/v1/trading/positions")
        raw = resp.content.decode("utf-8")
        assert "Decimal" not in raw, f"Decimal leaked into response body: {raw}"
        # Sanity: quantity appears as a number, not a quoted string.
        assert '"quantity": 1.5' in raw or '"quantity":1.5' in raw, (
            f"quantity not serialized as number: {raw}"
        )

    def test_positions_empty_when_no_engine(self) -> None:
        """No live engine → empty list (frontend renders empty state)."""
        engine = _FakeEngine(position=None)
        app = _make_app(engine)
        client = TestClient(app)

        resp = client.get("/api/v1/trading/positions")
        assert resp.status_code == 200
        assert resp.json() == []
