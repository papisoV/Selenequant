"""P2-5: StartupReconciler chaos scenarios — beyond the happy path of P0-5.

P0-5 covered the basic reconcile cases (clean, intent-never-sent, submitted
recovered, balance/position mismatch, exchange API failure). This module
adds chaos / edge-case scenarios that production crash recovery must handle:

- Multiple pending intents submitted simultaneously (no double-recovery).
- Submitted order partially filled on exchange (still open → recovered).
- Orphaned exchange order AND missing local order simultaneously.
- Idempotency: reconcile() called twice → second call is a no-op.
- State drift: local persisted order has stale status / qty.
- Mixed scenarios: pending intent + orphan + balance mismatch all at once.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.execution.startup_reconciliation import StartupReconciler


# ---------------------------------------------------------------------------
# Mock helpers (mirror test_startup_reconciliation.py conventions)
# ---------------------------------------------------------------------------


def _balance(asset: str, free: float) -> SimpleNamespace:
    return SimpleNamespace(asset=asset, free=free, locked=0.0)


def _position(symbol: str, pos_side: str, qty: float) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        pos_side=SimpleNamespace(value=pos_side),
        quantity=qty,
        avg_entry_price=100.0,
        unrealized_pnl=0.0,
    )


def _ex_order(order_id: str, symbol: str = "BTCUSDT") -> SimpleNamespace:
    return SimpleNamespace(order_id=order_id, symbol=symbol)


def _journal_entry(
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
    def __init__(
        self,
        orders: dict | None = None,
        fills: list | None = None,
        positions: list | None = None,
        balances: list | None = None,
        id_mappings: tuple[dict, dict] | None = None,
    ) -> None:
        self._orders = orders or {}
        self._fills = fills or []
        self._positions = positions or []
        self._balances = balances or []
        self._id_mappings = id_mappings or ({}, {})

    def load_orders(self) -> dict:
        return dict(self._orders)

    def load_fills(self) -> list:
        return list(self._fills)

    def load_positions(self) -> list:
        return list(self._positions)

    def load_balances(self) -> list:
        return list(self._balances)

    def load_id_mappings(self) -> tuple[dict, dict]:
        return self._id_mappings


class _MockClient:
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
# Chaos tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_pending_intents_all_processed() -> None:
    """3 pending INTENT entries → all 3 marked failed, 3 discrepancies recorded."""
    pending = [
        _journal_entry("co-1", status="intent"),
        _journal_entry("co-2", status="intent"),
        _journal_entry("co-3", status="intent"),
    ]
    client = _MockClient(open_orders=[], balances=[], positions=[])
    state = _MockStateRepo()
    journal = _MockJournal(pending=pending)

    report = await StartupReconciler().reconcile(client, state, journal)

    assert len(journal.failed) == 3
    failed_ids = {co for co, _ in journal.failed}
    assert failed_ids == {"co-1", "co-2", "co-3"}
    intent_discps = [d for d in report.discrepancies if d["type"] == "intent_never_sent"]
    assert len(intent_discps) == 3


@pytest.mark.asyncio
async def test_multiple_submitted_orders_recovered_without_double_marking() -> None:
    """3 SUBMITTED orders all still open on exchange → 3 recovered, none confirmed.

    This is the key chaos scenario: if reconcile marked confirmed orders
    that are still on exchange, a second reconcile would re-process them.
    """
    pending = [
        _journal_entry("co-1", status="submitted", exchange_order_id="ex-1"),
        _journal_entry("co-2", status="submitted", exchange_order_id="ex-2"),
        _journal_entry("co-3", status="submitted", exchange_order_id="ex-3"),
    ]
    client = _MockClient(
        open_orders=[_ex_order("ex-1"), _ex_order("ex-2"), _ex_order("ex-3")],
        balances=[],
        positions=[],
    )
    state = _MockStateRepo()
    journal = _MockJournal(pending=pending)

    report = await StartupReconciler().reconcile(client, state, journal)

    assert report.orders_recovered == 3
    # None should be marked confirmed — they're all still open on exchange.
    assert journal.confirmed == []


@pytest.mark.asyncio
async def test_submitted_order_partially_filled_still_open_recovered() -> None:
    """SUBMITTED order partially filled but still open → recovered (not confirmed).

    Real-world chaos: crash mid-fill. Exchange has the order with status
    PARTIAL. Reconcile sees it in open_orders → treats as recovered.
    """
    pending = _journal_entry("co-p", status="submitted", exchange_order_id="ex-p")
    client = _MockClient(
        open_orders=[_ex_order("ex-p")],  # still open (partial)
        balances=[],
        positions=[],
    )
    state = _MockStateRepo()
    journal = _MockJournal(pending=[pending])

    report = await StartupReconciler().reconcile(client, state, journal)

    assert report.orders_recovered == 1
    assert journal.confirmed == []


@pytest.mark.asyncio
async def test_orphan_and_missing_simultaneously() -> None:
    """Exchange has an orphan AND we have a submitted order missing on exchange.

    Two independent issues in one reconcile call — both must be flagged.
    """
    pending = _journal_entry("co-x", status="submitted", exchange_order_id="ex-x")
    client = _MockClient(
        open_orders=[_ex_order("ex-orphan", symbol="ETHUSDT")],  # not ours
        balances=[],
        positions=[],
    )
    state = _MockStateRepo()
    journal = _MockJournal(pending=[pending])

    report = await StartupReconciler().reconcile(client, state, journal)

    # Orphan detected.
    assert len(report.orphaned_exchange_orders) == 1
    assert report.orphaned_exchange_orders[0]["exchange_order_id"] == "ex-orphan"
    # Missing submitted order marked failed (Phase 2.5 fix: was mark_confirmed,
    # now mark_failed to avoid feeding phantom fills to RiskEngine).
    assert ("co-x", "submitted_order_missing_on_exchange") in journal.failed


@pytest.mark.asyncio
async def test_idempotent_second_reconcile_is_no_op() -> None:
    """Calling reconcile() twice: second call must not double-mark.

    First call processes pending entries (marks confirmed/failed). Second
    call sees an empty pending list (journal was updated) → no-op.

    We simulate this by giving the journal a "pop" semantics: after the
    first call, get_pending_entries returns []. The mock captures this by
    tracking call count.
    """
    pending = [_journal_entry("co-1", status="submitted", exchange_order_id="ex-1")]
    client = _MockClient(
        open_orders=[_ex_order("ex-1")],
        balances=[],
        positions=[],
    )
    state = _MockStateRepo()
    journal = _MockJournal(pending=pending)

    reconciler = StartupReconciler()
    report1 = await reconciler.reconcile(client, state, journal)

    # First call: 1 order recovered, no confirms.
    assert report1.orders_recovered == 1
    assert journal.confirmed == []

    # Simulate journal cleanup after first reconcile — pending is now empty.
    journal._pending = []
    report2 = await reconciler.reconcile(client, state, journal)

    # Second call: no pending, nothing new to recover.
    assert report2.orders_recovered == 0
    assert journal.confirmed == []
    # Orphan detection still fires on second call (same orphan).
    assert len(report2.orphaned_exchange_orders) == 1


@pytest.mark.asyncio
async def test_state_drift_local_qty_differs_from_exchange() -> None:
    """Local persisted order has stale quantity — reconcile doesn't currently
    detect qty drift (only orphan/missing), but must not crash or duplicate.

    Documents the current behavior: qty drift in local state is NOT flagged.
    The exchange is the source of truth for execution; local qty is just
    a cache. This test pins the behavior so a future change is deliberate.
    """
    # Local has order with qty=1.0; exchange has the same order_id but
    # qty=2.0 (drifted). Reconcile currently does NOT compare qty.
    local_order = SimpleNamespace(id="ex-drift", quantity=1.0, symbol="BTCUSDT")
    state = _MockStateRepo(
        orders={"ex-drift": local_order},
        id_mappings=({"internal-1": "ex-drift"}, {"ex-drift": "internal-1"}),
    )
    client = _MockClient(
        open_orders=[_ex_order("ex-drift")],  # exchange has it
        balances=[],
        positions=[],
    )
    journal = _MockJournal(pending=[])

    report = await StartupReconciler().reconcile(client, state, journal)

    # Order is known to us (in id_mappings) → NOT orphaned.
    assert len(report.orphaned_exchange_orders) == 0
    # No discrepancy flagged (qty drift is not currently detected).
    qty_drift_discps = [
        d for d in report.discrepancies if d.get("type") == "qty_drift"
    ]
    assert len(qty_drift_discps) == 0


@pytest.mark.asyncio
async def test_mixed_chaos_all_issues_at_once() -> None:
    """All chaos scenarios in one reconcile call:

    - 1 pending INTENT (process crashed before REST)
    - 1 pending SUBMITTED still open on exchange (recovered)
    - 1 pending SUBMITTED missing on exchange (marked failed — Phase 2.5 fix)
    - 1 orphaned exchange order
    - 1 balance mismatch
    """
    pending = [
        _journal_entry("co-intent", status="intent"),
        _journal_entry("co-open", status="submitted", exchange_order_id="ex-open"),
        _journal_entry("co-missing", status="submitted", exchange_order_id="ex-missing"),
    ]
    client = _MockClient(
        open_orders=[
            _ex_order("ex-open"),       # ours, still open
            _ex_order("ex-orphan"),     # not ours
        ],
        balances=[_balance("USDT", 500.0)],
        positions=[],
    )
    state = _MockStateRepo(balances=[_balance("USDT", 1000.0)])
    journal = _MockJournal(pending=pending)

    report = await StartupReconciler().reconcile(client, state, journal)

    # INTENT → mark_failed.
    assert ("co-intent", "Process crashed before REST call") in journal.failed
    # SUBMITTED open → recovered (not confirmed).
    assert report.orders_recovered == 1
    # SUBMITTED missing → failed (Phase 2.5 fix: was mark_confirmed, now mark_failed).
    assert ("co-missing", "submitted_order_missing_on_exchange") in journal.failed
    assert "co-open" not in journal.confirmed
    # Orphan detected.
    assert len(report.orphaned_exchange_orders) == 1
    assert report.orphaned_exchange_orders[0]["exchange_order_id"] == "ex-orphan"
    # Balance mismatch.
    assert report.balance_mismatch


@pytest.mark.asyncio
async def test_orphan_detection_via_id_mappings_only() -> None:
    """Orphan check uses BOTH local_orders IDs and id_mappings exchange_map.

    An exchange order whose ID is in id_mappings (but not in local_orders
    directly) must NOT be flagged as orphan — it's a known mapping.
    """
    state = _MockStateRepo(
        orders={},  # no local orders directly
        id_mappings=(
            {"internal-1": "ex-known"},  # client_order_id → exchange_order_id
            {"ex-known": "internal-1"},  # exchange_order_id → internal_id
        ),
    )
    client = _MockClient(
        open_orders=[_ex_order("ex-known"), _ex_order("ex-unknown")],
        balances=[],
        positions=[],
    )
    journal = _MockJournal(pending=[])

    report = await StartupReconciler().reconcile(client, state, journal)

    # ex-known is in id_mappings → not orphaned.
    # ex-unknown is not → orphaned.
    orphan_ids = [o["exchange_order_id"] for o in report.orphaned_exchange_orders]
    assert "ex-known" not in orphan_ids
    assert "ex-unknown" in orphan_ids


@pytest.mark.asyncio
async def test_balance_mismatch_within_tolerance_no_flag() -> None:
    """Balance diff < $1 tolerance → not flagged as mismatch."""
    client = _MockClient(
        open_orders=[],
        balances=[_balance("USDT", 999.5)],
        positions=[],
    )
    state = _MockStateRepo(balances=[_balance("USDT", 1000.0)])  # diff 0.5 < 1.0
    journal = _MockJournal(pending=[])

    report = await StartupReconciler().reconcile(client, state, journal)

    assert not report.balance_mismatch


@pytest.mark.asyncio
async def test_reconcile_handles_empty_state_gracefully() -> None:
    """Empty local state + empty exchange state + empty journal → clean report."""
    client = _MockClient(open_orders=[], balances=[], positions=[])
    state = _MockStateRepo()
    journal = _MockJournal(pending=[])

    report = await StartupReconciler().reconcile(client, state, journal)

    assert report.discrepancies == []
    assert report.orphaned_exchange_orders == []
    assert not report.balance_mismatch
    assert not report.position_mismatch
    assert report.orders_recovered == 0
