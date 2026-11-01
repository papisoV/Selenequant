"""Tests for LiveExecutionEngine journal + state_repo integration.

Verifies the crash-safe order pipeline:
  append_intent → REST → mark_submitted → save_order
  handle_exchange_order_update → mark_confirmed → save_fill
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.execution.live_state import LiveStateRepository
from selene_space_exchange.execution.order_journal import OrderJournal
from selene_space_exchange.trading import (
    BalanceInfo,
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import (
    ExchangeOrderUpdate,
    OrderSide,
    OrderStatus,
    OrderType,
)


class MockFailingClient(TradingClient):
    """Client that fails place_order to test journal FAILED path."""

    @property
    def exchange_name(self) -> str:
        return "mock-fail"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        raise RuntimeError("exchange unreachable")

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raise NotImplementedError

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raise NotImplementedError

    async def get_open_orders(self, symbol=None) -> list[OrderResponse]:  # type: ignore[override]
        return []

    async def get_balances(self) -> list[BalanceInfo]:
        return []

    async def test_connectivity(self) -> bool:
        return True


class MockSuccessClient(TradingClient):
    """Client that succeeds place_order and records calls."""

    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []

    @property
    def exchange_name(self) -> str:
        return "mock-ok"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        return OrderResponse(
            exchange="mock-ok",
            order_id="exch-1",
            client_order_id=request.client_order_id or "",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.ACCEPTED,
            quantity=request.quantity,
            price=request.price,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raise NotImplementedError

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raise NotImplementedError

    async def get_open_orders(self, symbol=None) -> list[OrderResponse]:  # type: ignore[override]
        return []

    async def get_balances(self) -> list[BalanceInfo]:
        return []

    async def test_connectivity(self) -> bool:
        return True


@pytest_asyncio.fixture
def journal(tmp_path: Path) -> OrderJournal:
    return OrderJournal(journal_dir=tmp_path / "journal")


@pytest_asyncio.fixture
def state_repo(tmp_path: Path) -> LiveStateRepository:
    return LiveStateRepository(state_dir=tmp_path / "live")


# ---------------------------------------------------------------------------
# place_order: journal FAILED path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_failure_marks_journal_failed(
    journal: OrderJournal,
    state_repo: LiveStateRepository,
) -> None:
    """When REST fails, journal entry must be marked FAILED."""
    engine = LiveExecutionEngine(
        MockFailingClient(),
        journal=journal,
        state_repo=state_repo,
    )

    with pytest.raises(RuntimeError, match="exchange unreachable"):
        await engine.place_order(
            account_id="acc-1",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
        )

    pending = journal.get_pending_entries()
    assert len(pending) == 0, "Failed entries should not be pending"

    # Verify the intent was written then marked failed (check via fresh connection)
    import sqlite3
    con = sqlite3.connect(str(journal._db_path))
    cur = con.execute("SELECT status, reason FROM order_journal WHERE client_order_id LIKE 'selenespace-%'")
    rows = cur.fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == "failed"
    assert "exchange unreachable" in rows[0][1]


# ---------------------------------------------------------------------------
# place_order: journal SUBMITTED path + state persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_success_marks_submitted_and_persists(
    journal: OrderJournal,
    state_repo: LiveStateRepository,
) -> None:
    """When REST succeeds, journal = SUBMITTED, state_repo has order + mapping."""
    client = MockSuccessClient()
    engine = LiveExecutionEngine(
        client,
        journal=journal,
        state_repo=state_repo,
    )

    order = await engine.place_order(
        account_id="acc-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )

    # Journal: 1 entry, status SUBMITTED, exchange_order_id set
    pending = journal.get_pending_entries()
    assert len(pending) == 1
    assert pending[0].status == "submitted"
    assert pending[0].exchange_order_id == "exch-1"
    assert pending[0].symbol == "BTC-USDT"

    # State repo: order persisted with mappings
    orders = state_repo.load_orders()
    assert order.id in orders
    assert orders[order.id].symbol == "BTC-USDT"

    client_map, exchange_map = state_repo.load_id_mappings()
    assert "exch-1" in exchange_map
    assert exchange_map["exch-1"] == order.id


# ---------------------------------------------------------------------------
# handle_exchange_order_update: journal CONFIRMED + fill persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_update_marks_confirmed_and_persists_fill(
    journal: OrderJournal,
    state_repo: LiveStateRepository,
) -> None:
    """WS fill update marks journal CONFIRMED and saves fill to state_repo."""
    client = MockSuccessClient()
    engine = LiveExecutionEngine(
        client,
        journal=journal,
        state_repo=state_repo,
    )

    order = await engine.place_order(
        account_id="acc-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )

    # Simulate WS fill update
    update = ExchangeOrderUpdate(
        exchange="binance",
        exchange_order_id="exch-1",
        client_order_id=next(
            co for co, iid in engine._client_to_internal.items() if iid == order.id
        ),
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        status="FILLED",
        quantity=0.01,
        filled_quantity=0.01,
        avg_fill_price=50000.0,
        last_fill_price=50000.0,
        last_fill_quantity=0.01,
        commission=0.005,
        commission_asset="USDT",
        timestamp=1_700_000_000_000,
    )
    await engine.handle_exchange_order_update(update)

    # Journal: no more pending (CONFIRMED removed from pending list)
    pending = journal.get_pending_entries()
    assert len(pending) == 0

    # State repo: fill persisted
    fills = state_repo.load_fills()
    assert len(fills) == 1
    assert fills[0].price == Decimal("50000.0")
    assert fills[0].quantity == Decimal("0.01")

    # State repo: order status updated to FILLED
    orders = state_repo.load_orders()
    assert orders[order.id].status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# Backward compatibility: journal=None still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_journal_backward_compat() -> None:
    """When journal=None, place_order still works (legacy behavior)."""
    client = MockSuccessClient()
    engine = LiveExecutionEngine(client)  # no journal, no state_repo

    order = await engine.place_order(
        account_id="acc-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    assert order.symbol == "BTC-USDT"
    assert order.status == OrderStatus.ACCEPTED
