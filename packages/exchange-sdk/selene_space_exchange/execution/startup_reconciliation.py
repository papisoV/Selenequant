"""Startup reconciliation for crash recovery.

Reconciles local persisted state with exchange state on process restart.
Ensures that orders, positions, and balances are consistent after an
unexpected shutdown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from selene_space_exchange.trading import TradingClient

from .live_state import LiveStateRepository
from .order_journal import OrderJournal

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    """Result of startup reconciliation."""

    orders_recovered: int = 0
    fills_recovered: int = 0
    discrepancies: list[dict[str, Any]] = field(default_factory=list)
    orphaned_exchange_orders: list[dict[str, Any]] = field(default_factory=list)
    missing_fills: list[dict[str, Any]] = field(default_factory=list)
    balance_mismatch: bool = False
    position_mismatch: bool = False


class StartupReconciler:
    """Reconciles local state with exchange on startup.

    Workflow:
      1. Load persisted orders from LiveStateRepository
      2. Check OrderJournal for INTENT/SUBMITTED entries (crash recovery)
      3. Fetch current exchange state via REST (balances, open orders, positions)
      4. Merge: trust exchange as source of truth
      5. Alert on discrepancies (orphaned orders, missing fills)

    Usage::

        reconciler = StartupReconciler()
        report = await reconciler.reconcile(client, state_repo, journal)
        if report.discrepancies:
            logger.warning("Found %d discrepancies", len(report.discrepancies))
    """

    async def reconcile(
        self,
        trading_client: TradingClient,
        state_repo: LiveStateRepository,
        journal: OrderJournal,
    ) -> ReconciliationReport:
        """Run full startup reconciliation.

        Args:
            trading_client: Exchange adapter for REST calls.
            state_repo: Persisted local state.
            journal: Order journal for pending entries.

        Returns:
            ReconciliationReport with findings.
        """
        report = ReconciliationReport()

        # 1. Load persisted local state
        local_orders = state_repo.load_orders()
        local_fills = state_repo.load_fills()
        local_positions = state_repo.load_positions()
        local_balances = state_repo.load_balances()

        logger.info(
            "Reconciliation: local state has %d orders, %d fills, %d positions",
            len(local_orders), len(local_fills), len(local_positions),
        )

        # 2. Check journal for pending entries
        pending = journal.get_pending_entries()
        if pending:
            logger.warning("Reconciliation: %d pending journal entries (crash recovery)", len(pending))
            report.discrepancies.append({
                "type": "pending_journal_entries",
                "count": len(pending),
                "entries": [
                    {"client_order_id": e.client_order_id, "status": e.status, "symbol": e.symbol}
                    for e in pending
                ],
            })

        # 3. Fetch exchange state via REST
        exchange_open_orders: list[Any] = []
        exchange_balances: list[Any] = []
        exchange_positions: list[Any] = []

        try:
            exchange_open_orders = await trading_client.get_open_orders()
        except Exception:
            logger.warning("Reconciliation: failed to fetch open orders from exchange")

        try:
            raw_balances = await trading_client.get_balances()
            exchange_balances = raw_balances
        except Exception:
            logger.warning("Reconciliation: failed to fetch balances from exchange")

        try:
            if hasattr(trading_client, "get_positions"):
                exchange_positions = await trading_client.get_positions()
        except Exception:
            logger.warning("Reconciliation: failed to fetch positions from exchange")

        logger.info(
            "Reconciliation: exchange has %d open orders, %d balances, %d positions",
            len(exchange_open_orders), len(exchange_balances), len(exchange_positions),
        )

        # 4. Reconcile journal pending entries
        for entry in pending:
            if entry.status == "intent":
                # Order was never sent to exchange — mark as failed
                journal.mark_failed(entry.client_order_id, reason="Process crashed before REST call")
                report.discrepancies.append({
                    "type": "intent_never_sent",
                    "client_order_id": entry.client_order_id,
                    "symbol": entry.symbol,
                })
            elif entry.status == "submitted":
                # Order was sent but confirmation was lost — check exchange
                found_on_exchange = False
                for ex_order in exchange_open_orders:
                    ex_oid = getattr(ex_order, "order_id", None) or getattr(ex_order, "id", None)
                    if ex_oid == entry.exchange_order_id:
                        found_on_exchange = True
                        break

                if found_on_exchange:
                    logger.info(
                        "Reconciliation: submitted order %s still open on exchange",
                        entry.client_order_id,
                    )
                    report.orders_recovered += 1
                else:
                    # Order was sent but is no longer open on the exchange.
                    # Do NOT assume filled — the order may have never reached
                    # the exchange (network failure mid-REST), may have been
                    # cancelled by exchange risk controls, or may have filled
                    # partially. Marking confirmed here would feed a phantom
                    # fill to RiskEngine, corrupting equity/PnL state.
                    # Safe action: mark failed and route to manual review queue.
                    journal.mark_failed(
                        entry.client_order_id,
                        reason="submitted_order_missing_on_exchange",
                    )
                    report.discrepancies.append({
                        "type": "submitted_order_missing_on_exchange",
                        "client_order_id": entry.client_order_id,
                        "exchange_order_id": entry.exchange_order_id,
                        "action": "marked_failed_pending_manual_review",
                        "unreconciled_submitted": True,
                    })

        # 5. Check for orphaned exchange orders (not in our journal)
        our_exchange_ids = set()
        for order in local_orders.values():
            our_exchange_ids.add(order.id)
        client_map, exchange_map = state_repo.load_id_mappings()
        our_exchange_ids.update(exchange_map.keys())
        # Pending SUBMITTED journal entries reference exchange_order_ids that
        # we placed but haven't yet confirmed via WS / REST. These must NOT
        # be flagged as orphans — they're our orders, just not yet persisted
        # to id_mappings (the crash happened before mark_submitted completed).
        for entry in pending:
            if getattr(entry, "exchange_order_id", None):
                our_exchange_ids.add(entry.exchange_order_id)

        for ex_order in exchange_open_orders:
            ex_oid = getattr(ex_order, "order_id", None) or getattr(ex_order, "id", None)
            if ex_oid and ex_oid not in our_exchange_ids:
                report.orphaned_exchange_orders.append({
                    "exchange_order_id": ex_oid,
                    "symbol": getattr(ex_order, "symbol", "unknown"),
                })

        # 6. Balance reconciliation
        if local_balances and exchange_balances:
            local_by_asset = {b.asset: b for b in local_balances}
            for ex_bal in exchange_balances:
                asset = getattr(ex_bal, "asset", "")
                ex_free = getattr(ex_bal, "free", 0.0)
                if asset in local_by_asset:
                    diff = abs(ex_free - local_by_asset[asset].free)
                    if diff > 1.0:  # $1 tolerance
                        report.balance_mismatch = True
                        report.discrepancies.append({
                            "type": "balance_mismatch",
                            "asset": asset,
                            "local_free": local_by_asset[asset].free,
                            "exchange_free": ex_free,
                            "diff": diff,
                        })

        # 7. Position reconciliation
        if local_positions and exchange_positions:
            local_pos_by_symbol = {f"{p.symbol}:{p.pos_side.value}": p for p in local_positions}
            for ex_pos in exchange_positions:
                sym = getattr(ex_pos, "symbol", getattr(ex_pos, "instId", ""))
                pos_side = getattr(ex_pos, "posSide", "net")
                key = f"{sym}:{pos_side}"
                if key not in local_pos_by_symbol:
                    report.position_mismatch = True
                    report.discrepancies.append({
                        "type": "exchange_position_not_in_local",
                        "symbol": sym,
                        "pos_side": pos_side,
                    })

        # Summary log
        total_issues = len(report.discrepancies) + len(report.orphaned_exchange_orders)
        if total_issues == 0:
            logger.info("Reconciliation: clean — no discrepancies found")
        else:
            logger.warning(
                "Reconciliation: %d issues found (%d discrepancies, %d orphaned orders)",
                total_issues, len(report.discrepancies), len(report.orphaned_exchange_orders),
            )

        return report
