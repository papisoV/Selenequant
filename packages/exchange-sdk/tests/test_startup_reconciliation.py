"""P0-5: StartupReconciler unit tests.

Verifies the crash-recovery reconciliation workflow in
startup_reconciliation.py:53-215 — mapping of exchange state back to
local state, detection of orphaned orders, balance/position mismatches,
and recovery of pending journal entries.

Uses lightweight mock objects (SimpleNamespace) for trading_client /
state_repo / journal so we exercise the reconcile() logic in isolation,
not the persistence layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.execution.startup_reconciliation import (
    ReconciliationReport,
    StartupReconciler,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_balance(asset: str, free: float, locked: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(asset=asset, free=free, locked=locked)


def _make_position(symbol: str, pos_side: str, qty: float) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        pos_side=SimpleNamespace(value=pos_side),
        quantity=qty,
        avg_entry_price=100.0,
        unrealized_pnl=0.0,
    )


def _make_exchange_order(order_id: str, symbol: str = "BTCUSDT") -> SimpleNamespace:
    return SimpleNamespace(order_id=order_id, symbol=symbol)


def _make_journal_entry(
    client_order_id: str,
    status: str,
    exchange_order_id: str | None = None,
    symbol: str = "BTCUSDT",
) -> SimpleNamespace:
    return SimpleNamespace(
        client_order_id=client_order_id,
        status=status,
        exchange_order_id=exchange_order_id,
        symbol=symbol,
    )


class _MockJournal:
    """Minimal journal mock — tracks mark_failed / mark_confirmed calls."""

    def __init__(self, pending: list) -> None:
        self._pending = pending
        self.failed: list[tuple[str, str]] = []
        self.confirmed: list[str] = []

    def get_pending_entries(self) -> list:
        return list(self._pending)

    def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        self.failed.append((client_order_id, reason))

    def mark_confirmed(self, client_order_id: str) -> None:
        self.confirmed.append(client_order_id)


class _MockStateRepo:
    """Minimal state repo mock — returns canned local state."""

    def __init__(
        self,
        orders: dict | None = None,
        fills: list | None = None,
        positions: list | None = None,
        balances: list | None = None,
    ) -> None:
        self._orders = orders or {}
        self._fills = fills or []
        self._positions = positions or []
        self._balances = balances or []

    def load_orders(self) -> dict:
        return dict(self._orders)

    def load_fills(self) -> list:
        return list(self._fills)

    def load_positions(self) -> list:
        return list(self._positions)

    def load_balances(self) -> list:
        return list(self._balances)

    def load_id_mappings(self) -> tuple[dict, dict]:
        return ({}, {})


class _MockTradingClient:
    """Minimal trading client mock — async methods for exchange state."""

    def __init__(
        self,
        open_orders: list | None = None,
        balances: list | None = None,
        positions: list | None = None,
    ) -> None:
        self._open_orders = open_orders or []
        self._balances = balances or []
        self._positions = positions or []
        self.get_open_orders = AsyncMock(return_value=self._open_orders)
        self.get_balances = AsyncMock(return_value=self._balances)
        self.get_positions = AsyncMock(return_value=self._positions)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_reconciliation_no_discrepancies() -> None:
    """When local + exchange + journal all agree, report is clean."""
    client = _MockTradingClient(
        open_orders=[],
        balances=[_make_balance("USDT", 1000.0)],
        positions=[],
    )
    state = _MockStateRepo(
        orders={},
        fills=[],
        positions=[],
        balances=[_make_balance("USDT", 1000.0)],
    )
    journal = _MockJournal(pending=[])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert isinstance(report, ReconciliationReport)
    assert report.discrepancies == []
    assert report.orphaned_exchange_orders == []
    assert not report.balance_mismatch
    assert not report.position_mismatch
    assert report.orders_recovered == 0


@pytest.mark.asyncio
async def test_intent_never_sent_marked_failed() -> None:
    """Pending INTENT entry (process crashed before REST) → mark_failed."""
    pending_intent = _make_journal_entry("co-1", status="intent")
    client = _MockTradingClient(open_orders=[], balances=[], positions=[])
    state = _MockStateRepo()
    journal = _MockJournal(pending=[pending_intent])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert ("co-1", "Process crashed before REST call") in journal.failed
    # Discrepancy recorded for the intent never sent.
    assert any(
        d["type"] == "intent_never_sent" and d["client_order_id"] == "co-1"
        for d in report.discrepancies
    )


@pytest.mark.asyncio
async def test_submitted_order_recovered_when_still_on_exchange() -> None:
    """Pending SUBMITTED entry found in exchange open orders → recovered."""
    pending_submitted = _make_journal_entry(
        "co-2", status="submitted", exchange_order_id="ex-100"
    )
    client = _MockTradingClient(
        open_orders=[_make_exchange_order("ex-100")],
        balances=[],
        positions=[],
    )
    state = _MockStateRepo()
    journal = _MockJournal(pending=[pending_submitted])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert report.orders_recovered == 1
    # Should NOT be marked confirmed (it's still on the exchange).
    assert "co-2" not in journal.confirmed


@pytest.mark.asyncio
async def test_submitted_order_missing_on_exchange_marked_failed() -> None:
    """Pending SUBMITTED entry NOT on exchange → mark_failed (Phase 2.5 fix).

    Previously this assumed the order had been filled and called mark_confirmed.
    That fed phantom fills to RiskEngine, corrupting equity/PnL state. The
    order may have never reached the exchange (network failure mid-REST),
    been cancelled by exchange risk controls, etc. Safe action: mark_failed
    + flag for manual review.
    """
    pending_submitted = _make_journal_entry(
        "co-3", status="submitted", exchange_order_id="ex-999"
    )
    client = _MockTradingClient(open_orders=[], balances=[], positions=[])
    state = _MockStateRepo()
    journal = _MockJournal(pending=[pending_submitted])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert ("co-3", "submitted_order_missing_on_exchange") in journal.failed
    assert "co-3" not in journal.confirmed
    disc = next(
        d for d in report.discrepancies
        if d.get("type") == "submitted_order_missing_on_exchange"
        and d.get("client_order_id") == "co-3"
    )
    assert disc["action"] == "marked_failed_pending_manual_review"
    assert disc["unreconciled_submitted"] is True


@pytest.mark.asyncio
async def test_orphaned_exchange_order_detected() -> None:
    """Exchange order not in our state/journal → flagged as orphaned."""
    client = _MockTradingClient(
        open_orders=[_make_exchange_order("ex-orphan", symbol="ETHUSDT")],
        balances=[],
        positions=[],
    )
    state = _MockStateRepo(orders={}, id_mappings=None) if False else _MockStateRepo()
    journal = _MockJournal(pending=[])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert len(report.orphaned_exchange_orders) == 1
    assert report.orphaned_exchange_orders[0]["exchange_order_id"] == "ex-orphan"


@pytest.mark.asyncio
async def test_balance_mismatch_detected() -> None:
    """Local vs exchange balance diff > $1 → balance_mismatch=True."""
    client = _MockTradingClient(
        open_orders=[],
        balances=[_make_balance("USDT", 500.0)],  # exchange says 500
        positions=[],
    )
    state = _MockStateRepo(balances=[_make_balance("USDT", 1000.0)])  # local says 1000
    journal = _MockJournal(pending=[])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert report.balance_mismatch
    assert any(
        d["type"] == "balance_mismatch" and d["asset"] == "USDT"
        for d in report.discrepancies
    )


@pytest.mark.asyncio
async def test_position_mismatch_detected() -> None:
    """Exchange position not in local state → position_mismatch=True.

    Note: reconcile() only runs the position block when BOTH local and
    exchange have positions (the `if local_positions and exchange_positions`
    guard at startup_reconciliation.py:191). So we seed local with a
    different symbol to make the block fire, then assert the exchange
    position is flagged as not-in-local.
    """
    client = _MockTradingClient(
        open_orders=[],
        balances=[],
        positions=[_make_position("BTCUSDT", "net", 0.5)],
    )
    # Local has a different symbol so the BTCUSDT exchange position
    # is not in local_pos_by_symbol → flagged as mismatch.
    state = _MockStateRepo(positions=[_make_position("ETHUSDT", "net", 2.0)])
    journal = _MockJournal(pending=[])

    reconciler = StartupReconciler()
    report = await reconciler.reconcile(client, state, journal)

    assert report.position_mismatch
    assert any(
        d["type"] == "exchange_position_not_in_local" and d["symbol"] == "BTCUSDT"
        for d in report.discrepancies
    )


@pytest.mark.asyncio
async def test_exchange_api_failure_is_non_fatal() -> None:
    """If exchange REST throws, reconciliation still completes with what we have."""
    client = _MockTradingClient()
    # Override to raise.
    client.get_open_orders = AsyncMock(side_effect=RuntimeError("exchange down"))
    client.get_balances = AsyncMock(side_effect=RuntimeError("exchange down"))
    client.get_positions = AsyncMock(side_effect=RuntimeError("exchange down"))

    state = _MockStateRepo()
    journal = _MockJournal(pending=[])

    reconciler = StartupReconciler()
    # Must not raise.
    report = await reconciler.reconcile(client, state, journal)
    assert isinstance(report, ReconciliationReport)
