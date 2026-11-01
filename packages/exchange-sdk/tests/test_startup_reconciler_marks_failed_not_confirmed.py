"""Tests for StartupReconciler Phase 2.5 fix: submitted order missing on exchange
must be marked FAILED (not confirmed) and flagged for manual review.

Background: prior implementation called journal.mark_confirmed() when a SUBMITTED
journal entry could not be located among exchange open orders. This assumed the
order had been filled, but in reality the order may have:
  - never reached the exchange (network failure mid-REST)
  - been cancelled by exchange risk controls
  - been cancelled by a prior reconciliation pass
Feeding a phantom fill to RiskEngine corrupts equity/PnL state. The fix marks
such entries as failed and routes them to a manual review queue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from selene_space_exchange.execution.live_state import LiveStateRepository
from selene_space_exchange.execution.order_journal import OrderJournal
from selene_space_exchange.execution.startup_reconciliation import (
    StartupReconciler,
)
from selene_space_exchange.trading import (
    BalanceInfo,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


class EmptyExchangeClient(TradingClient):
    """Exchange client that reports zero open orders (none found on exchange)."""

    @property
    def exchange_name(self) -> str:
        return "mock-empty"

    async def place_order(self, request: Any) -> OrderResponse:  # type: ignore[override]
        raise NotImplementedError

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:  # type: ignore[override]
        raise NotImplementedError

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:  # type: ignore[override]
        raise NotImplementedError

    async def get_open_orders(self, symbol: Any = None) -> list[OrderResponse]:  # type: ignore[override]
        # Key: NO open orders — the SUBMITTED journal entry is missing on exchange
        return []

    async def get_balances(self) -> list[BalanceInfo]:  # type: ignore[override]
        return []

    async def test_connectivity(self) -> bool:
        return True


@pytest.fixture
def journal(tmp_path: Path) -> OrderJournal:
    return OrderJournal(journal_dir=tmp_path / "journal")


@pytest.fixture
def state_repo(tmp_path: Path) -> LiveStateRepository:
    return LiveStateRepository(state_dir=tmp_path / "live")


@pytest.mark.asyncio
async def test_submitted_missing_on_exchange_marks_failed_not_confirmed(
    journal: OrderJournal,
    state_repo: LiveStateRepository,
) -> None:
    """When a SUBMITTED journal entry is missing on exchange, reconciler must
    mark_failed (NOT mark_confirmed) and add the unreconciled_submitted flag."""
    # Seed: order was sent to exchange (SUBMITTED) but is not in open orders now
    journal.append_intent(
        client_order_id="co-missing",
        symbol="BTC-USDT-SWAP",
        side="buy",
        order_type="limit",
        quantity=1.0,
        price=65000.0,
        account_id="pa-abc",
    )
    journal.mark_submitted("co-missing", exchange_order_id="ex-GHOST")

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(EmptyExchangeClient(), state_repo, journal)

    # Verify the journal entry is now FAILED (not confirmed)
    entry = journal.get_entry("co-missing")
    assert entry is not None
    assert entry.status == "failed", (
        f"expected failed, got {entry.status} — reconciler must NOT mark_confirmed "
        "when order is missing on exchange"
    )
    assert entry.reason == "submitted_order_missing_on_exchange"

    # Verify the discrepancy dict carries the new action + flag
    matching = [
        d for d in report.discrepancies
        if d.get("type") == "submitted_order_missing_on_exchange"
    ]
    assert len(matching) == 1, f"expected 1 discrepancy, got {len(matching)}"
    disc = matching[0]
    assert disc["action"] == "marked_failed_pending_manual_review"
    assert disc["unreconciled_submitted"] is True
    assert disc["client_order_id"] == "co-missing"
    assert disc["exchange_order_id"] == "ex-GHOST"


@pytest.mark.asyncio
async def test_submitted_still_open_on_exchange_not_marked_failed(
    journal: OrderJournal,
    state_repo: LiveStateRepository,
) -> None:
    """Sanity check: when the SUBMITTED order IS still open on exchange,
    reconciler must NOT mark it failed — it should be recovered."""

    class StillOpenClient(EmptyExchangeClient):
        async def get_open_orders(self, symbol: Any = None) -> list[OrderResponse]:  # type: ignore[override]
            return [
                OrderResponse(
                    exchange="mock-empty",
                    order_id="ex-LIVE",
                    symbol="BTC-USDT-SWAP",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    status=OrderStatus.ACCEPTED,
                    quantity=1.0,
                    price=65000.0,
                )
            ]

    journal.append_intent(
        client_order_id="co-live",
        symbol="BTC-USDT-SWAP",
        side="buy",
        order_type="limit",
        quantity=1.0,
        price=65000.0,
        account_id="pa-abc",
    )
    journal.mark_submitted("co-live", exchange_order_id="ex-LIVE")

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(StillOpenClient(), state_repo, journal)

    entry = journal.get_entry("co-live")
    assert entry is not None
    assert entry.status == "submitted", (
        f"expected submitted (still open), got {entry.status} — reconciler must not "
        "fail an order that is still open on the exchange"
    )
    assert report.orders_recovered == 1
