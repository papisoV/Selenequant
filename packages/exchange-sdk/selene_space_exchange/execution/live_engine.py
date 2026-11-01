"""Live execution engine — real exchange trading with WS-first order tracking.

Places orders via REST and tracks their lifecycle primarily through private
WebSocket channels. REST is used as a fallback for reconciliation.

Architecture:
    place_order() → REST API → exchange
    exchange → Private WS → on_order_update callback → internal state update
    reconciliation → REST API (on reconnect / periodic)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from selene_space_exchange.base.reconciliation import ReconciliationManager
from selene_space_exchange.exceptions import PreTradeRiskBlockError
from selene_space_exchange.execution.disconnect_triage import (
    DisconnectLevel,
    classify_unknown_order,
    should_warn_outside_order,
    triage_disconnect,
)
from selene_space_exchange.trading import (
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import (
    AccountSnapshot,
    BalanceUpdate,
    ExchangeOrderUpdate,
    ExecutionMode,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

if TYPE_CHECKING:
    from selene_space_exchange.execution.live_state import LiveStateRepository
    from selene_space_exchange.execution.order_journal import OrderJournal

logger = logging.getLogger(__name__)


class LiveExecutionEngine:
    """ExecutionEngine implementation for live (real exchange) trading.

    WS-first architecture:
    - Orders placed via REST (TradingClient)
    - Status tracked via private WS (callbacks wired externally)
    - REST reconciliation as fallback (ReconciliationManager)
    - Local state maintained as client_order_id → Order mapping
    """

    # Live engine does NOT accept mark_price/signal_timestamp_ms — orders
    # go to the real exchange via REST and fill at the exchange's price,
    # not a caller-supplied one. StrategyRunner uses this flag to pick
    # the right place_order signature.
    supports_mark_price: bool = False

    def __init__(
        self,
        trading_client: TradingClient,
        *,
        reconciliation_interval_s: float = 300.0,
        journal: "OrderJournal | None" = None,
        state_repo: "LiveStateRepository | None" = None,
        risk_engine: Any | None = None,
        kill_switch: Any | None = None,
        ws_health_monitor: Any | None = None,
        router: Any | None = None,
        quote_provider: Any | None = None,
        venues: dict[str, TradingClient] | None = None,
        fills_ledger: Any | None = None,
        equity_tracker: Any | None = None,
        flatten_hook: Any | None = None,
        client_order_prefixes: list[str] | None = None,
        disconnect_grace_seconds: int = 900,
    ) -> None:
        self._client = trading_client
        self._reconciliation_interval = reconciliation_interval_s
        self._reconciliation: ReconciliationManager | None = None

        # Crash-safe journal + persistent state (optional for backward compat).
        # When None, LiveEngine behaves as before (no journal, no state).
        self._journal = journal
        self._state_repo = state_repo

        # Pre-trade risk gate (optional). When None, no pre-trade check is
        # performed — backward compat for tests / shadow mode without risk.
        # Wired via constructor OR setter (main.py assembles risk_engine
        # after LiveEngine, then calls set_risk_engine).
        self._risk_engine = risk_engine
        self._kill_switch = kill_switch

        # Phase 3: WS health monitor (optional). When None, no WS health gate
        # is performed — backward compat for tests / shadow mode. When set,
        # place_order refuses to submit if WS is degraded (Binance 1008
        # rate limit / network partition protection).
        self._ws_health_monitor = ws_health_monitor

        # Phase 2b: FillsLedger (persistent fill records → daily_realized_pnl)
        # and EquityTracker (monotonic peak + UTC daily reset → peak_equity).
        # When None, risk context falls back to 0.0 / total_equity — backward
        # compat for tests / shadow mode without realized PnL or drawdown.
        self._fills_ledger = fills_ledger
        self._equity_tracker = equity_tracker

        # Smart Order Router (P2-3). When router + quote_provider + venues
        # are configured and place_order is called with metadata={"route": True},
        # the engine fetches quotes via quote_provider, selects the best venue
        # via router.route_order, and submits through that venue's client
        # instead of the default ``trading_client``. When any of the three is
        # None, or metadata doesn't request routing, the default client path
        # is used — fully backward compatible.
        self._router = router
        self._quote_provider = quote_provider
        self._venues = venues or {}

        # Order tracker for self-trade prevention. Maintained alongside
        # self._orders so the risk gate can detect cross-strategy conflicts
        # before the order reaches the exchange.
        from selene_space_risk.self_trade import OrderTracker

        self._order_tracker = OrderTracker()

        # Internal state
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._balances: list[BalanceUpdate] = []
        self._positions: list[Position] = []

        # Mapping: client_order_id → internal_order_id
        self._client_to_internal: dict[str, str] = {}
        # Mapping: exchange_order_id → internal_order_id
        self._exchange_to_internal: dict[str, str] = {}

        # Event callbacks
        self._on_order_update_cbs: list[Callable[[Order], Any]] = []
        self._on_fill_cbs: list[Callable[[Fill], Any]] = []
        self._on_position_update_cbs: list[Callable[[Position], Any]] = []
        self._on_balance_update_cbs: list[Callable[[list[BalanceUpdate]], Any]] = []

        # Phase 6: per-session first-trade approval gate. Starts False on
        # every engine construction (incl. restart). Must be set True via
        # approve_first_trade() before place_order() will submit to the
        # exchange. Frontend confirmation dialog calls POST /api/v1/live/approve
        # which flips this flag. Prevents accidental real orders after a
        # restart without explicit user confirmation.
        self._first_trade_approved: bool = False

        # C3 fix: per-user approve set. The engine is a singleton shared
        # across users; ``_first_trade_approved`` alone approves globally.
        # ``approve_first_trade(user_id)`` adds to this set; ``place_order``
        # checks membership. A user not in the set gets blocked even if
        # another user already approved. Backward compat: if the set is empty
        # AND _first_trade_approved is True (legacy path), allow — this
        # preserves the old behavior for callers that never pass user_id.
        self._approved_users: set[str] = set()

        # Bug C: StopManager for local stop-loss (injected via setter).
        # None until set_stop_manager() called; place_order/on_price_tick
        # treat None as no-op (backward compat for tests / shadow mode).
        self._stop_manager: Any | None = None
        # v3 BLOCK B fix: hold strong refs to fire-and-forget stop-close
        # tasks so Python GC doesn't cancel them mid-flight. Cleared via
        # done_callback when task completes.
        self._pending_stop_tasks: set[asyncio.Task] = set()

        # US-002: REST reconciliation fallback task. Polls /fapi/v1/allOrders
        # every N seconds to catch fills that WS user data stream dropped
        # (proxy instability, reconnect gaps). Triggers simulate_fill for
        # any order that is FILLED on exchange but still ACCEPTED locally.
        self._rest_reconciliation_task: asyncio.Task | None = None
        self._rest_reconciliation_interval_s: float = 30.0

        # P2-1: WS disconnect triage. When the private WS drops with risk
        # exposure (open positions / open orders), a grace timer starts;
        # if it expires without a reconnect, the injected flatten_hook is
        # fired as close-protection. Reconnect cancels the timer. Client
        # order prefixes identify orders this system placed — everything
        # else is treated as a manual (outside) order and isolated.
        self._flatten_hook = flatten_hook
        self._client_order_prefixes = client_order_prefixes or ["selenespace-"]
        self._disconnect_grace_seconds = disconnect_grace_seconds
        self._disconnect_grace_task: asyncio.Task | None = None
        self._disconnected_at_ms: int | None = None

    # ------------------------------------------------------------------
    # Phase 6: first-trade approval gate
    # ------------------------------------------------------------------

    def approve_first_trade(self, user_id: str | None = None) -> None:
        """Mark this session as having user approval for real orders.

        Idempotent — calling multiple times has no extra effect. Resets on
        engine restart (constructor sets it back to False).

        C3 fix: if ``user_id`` is provided, the user is added to
        ``_approved_users`` (per-user gate). If None, falls back to the
        legacy global flag (``_first_trade_approved = True``) for backward
        compat with callers that don't pass user_id.
        """
        self._first_trade_approved = True
        if user_id is not None:
            self._approved_users.add(user_id)
            logger.warning(
                "Phase 6: first-trade approval granted for user=%s (per-user)",
                user_id,
            )
        else:
            logger.warning(
                "Phase 6: first-trade approval granted for this session (global)"
            )

    @property
    def first_trade_approved(self) -> bool:
        """Read-only view of approval state for /live/approval-status."""
        return self._first_trade_approved

    # ------------------------------------------------------------------
    # Risk gate setters (called by main.py after risk_engine assembly)
    # ------------------------------------------------------------------

    def set_risk_engine(self, risk_engine: Any | None) -> None:
        """Inject the RiskEngine after construction.

        main.py assembles RiskEngine after LiveEngine (order constraint:
        RiskEngine needs rules loaded from saved settings). This setter
        lets the assembled risk engine be wired back into the live engine
        without reordering the lifespan.
        """
        self._risk_engine = risk_engine

    def set_kill_switch(self, kill_switch: Any | None) -> None:
        """Inject the KillSwitch after construction."""
        self._kill_switch = kill_switch

    def set_ws_health_monitor(self, monitor: Any | None) -> None:
        """Inject the WsHealthMonitor after construction (Phase 3)."""
        self._ws_health_monitor = monitor

    def set_fills_ledger(self, fills_ledger: Any | None) -> None:
        """Inject the FillsLedger after construction (Phase 2b)."""
        self._fills_ledger = fills_ledger

    def set_equity_tracker(self, equity_tracker: Any | None) -> None:
        """Inject the EquityTracker after construction (Phase 2b)."""
        self._equity_tracker = equity_tracker

    def set_router(
        self,
        router: Any | None,
        quote_provider: Any | None = None,
        venues: dict[str, TradingClient] | None = None,
    ) -> None:
        """Inject the Smart Order Router + quote provider + venue clients.

        All three are required for routing to activate. Any subset may be
        None to disable routing — ``place_order(metadata={"route": True})``
        will fall back to the default client path.
        """
        self._router = router
        if quote_provider is not None:
            self._quote_provider = quote_provider
        if venues is not None:
            self._venues = venues

    def set_stop_manager(self, stop_manager: Any | None) -> None:
        """Inject the StopManager after construction (Bug C integration).

        StopManager manages server-side stop-loss orders. When None,
        place_order does not register stops and on_price_tick is a no-op
        — backward compat for tests / shadow mode without stop management.
        """
        self._stop_manager = stop_manager

    def set_flatten_hook(self, flatten_hook: Any | None) -> None:
        """Inject the FlattenHook after construction (P2-1 disconnect triage).

        Fired as close-protection when the WS disconnect grace window
        expires without a reconnect. When None, the engine only logs
        CRITICAL on grace expiry.
        """
        self._flatten_hook = flatten_hook

    # ------------------------------------------------------------------
    # ExecutionEngine interface
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.LIVE

    @property
    def exchange(self) -> str:
        return self._client.exchange_name

    async def start(self) -> None:
        # Bug C: restore active stops from state_repo before reconciliation
        # so restart doesn't lose stop-loss protection. Pre-mortem 场景 4.
        if self._state_repo is not None and self._stop_manager is not None:
            try:
                stops = self._state_repo.load_stops()
                from selene_space_exchange.execution.stop_manager import StopStatus

                for handle in stops:
                    if handle.status == StopStatus.ACTIVE:
                        self._stop_manager._stops[handle.stop_id] = handle
                logger.warning(
                    "StopManager first-time integration: %d active stops loaded",
                    len(self._stop_manager.list_active_stops()),
                )
            except Exception as exc:
                logger.error("Failed to load stops from state_repo: %s", exc)

        self._reconciliation = ReconciliationManager(
            self._client,
            on_snapshot=self._handle_reconciliation_snapshot,
            on_orders=self._handle_reconciliation_orders,
        )
        await self._reconciliation.start_periodic(self._reconciliation_interval)

        # Initial reconciliation
        await self._reconciliation.reconcile_on_reconnect()
        logger.info("LiveExecutionEngine started for %s", self.exchange)

        # US-002: REST reconciliation fallback — catches fills WS dropped.
        # Only start if the client exposes get_all_orders (BinanceFutures
        # does; spot/BinanceTradingAdapter doesn't).
        if hasattr(self._client, "get_all_orders"):
            self._rest_reconciliation_task = asyncio.create_task(
                self._rest_reconciliation_loop()
            )
            logger.info(
                "REST reconciliation fallback started (interval=%ss)",
                self._rest_reconciliation_interval_s,
            )

    async def stop(self) -> None:
        if self._reconciliation:
            await self._reconciliation.stop()
        if self._rest_reconciliation_task is not None:
            self._rest_reconciliation_task.cancel()
            try:
                await self._rest_reconciliation_task
            except (asyncio.CancelledError, Exception):
                pass
            self._rest_reconciliation_task = None
        await self._cancel_grace_timer()
        logger.info("LiveExecutionEngine stopped for %s", self.exchange)

    # ------------------------------------------------------------------
    # P2-1: WS disconnect triage
    # ------------------------------------------------------------------

    async def handle_ws_disconnect(self, *, grace_seconds: int | None = None) -> None:
        """Triage a private WS disconnect by current risk exposure.

        - Open exposure → start a grace timer; expiry triggers CLOSE_PROTECT
          via the injected flatten_hook (or a CRITICAL log when none).
        - Flat book → SILENT_RETRY, no timer, no alarm.

        Wire this to the WS client's on_disconnect chain.
        """
        grace = (
            grace_seconds
            if grace_seconds is not None
            else self._disconnect_grace_seconds
        )
        now_ms = int(time.time() * 1000)
        has_positions = any(p.quantity > 0 for p in self._positions)
        has_open_orders = any(
            o.status
            not in (
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
            )
            for o in self._orders.values()
        )
        outcome = triage_disconnect(
            has_positions=has_positions,
            has_open_orders=has_open_orders,
            disconnected_at_ms=now_ms,
            now_ms=now_ms,
            grace_seconds=grace,
        )
        if outcome.level is DisconnectLevel.GRACE:
            # Re-disconnect while a timer is pending resets the window:
            # cancel the old timer before starting a fresh one.
            await self._cancel_grace_timer()
            self._disconnected_at_ms = now_ms
            logger.warning(
                "WS disconnect with risk exposure — entering %ss grace window (reason=%s)",
                outcome.grace_seconds,
                outcome.reason,
            )
            self._disconnect_grace_task = asyncio.create_task(
                self._grace_timer(outcome.grace_seconds)
            )
        else:
            logger.info(
                "WS disconnect triaged as %s (reason=%s)",
                outcome.level.value,
                outcome.reason,
            )

    async def handle_ws_reconnect(self) -> None:
        """Reconnect success — cancel the grace timer and reset triage state.

        Call before reconciliation on reconnect.
        """
        await self._cancel_grace_timer()
        self._disconnected_at_ms = None
        logger.info("WS reconnect — grace timer cancelled, triage reset")

    async def _grace_timer(self, grace_seconds: int) -> None:
        """Await the grace window, then trigger CLOSE_PROTECT flatten."""
        try:
            await asyncio.sleep(grace_seconds)
        except asyncio.CancelledError:
            return
        disconnected_at = self._disconnected_at_ms
        if disconnected_at is None:
            return  # already reconnected — timer cancelled
        now_ms = int(time.time() * 1000)
        outcome = triage_disconnect(
            has_positions=any(p.quantity > 0 for p in self._positions),
            has_open_orders=any(
                o.status
                not in (
                    OrderStatus.FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.REJECTED,
                    OrderStatus.EXPIRED,
                )
                for o in self._orders.values()
            ),
            disconnected_at_ms=disconnected_at,
            now_ms=now_ms,
            grace_seconds=grace_seconds,
        )
        if outcome.level is DisconnectLevel.CLOSE_PROTECT:
            self._trigger_close_protect(outcome.reason)

    def _trigger_close_protect(self, reason: str) -> None:
        """Fire the flatten hook as close-protection (fire-and-forget).

        The engine only *triggers* protection; executing the flatten is the
        risk-engine's FlattenHook callback's job. No hook → CRITICAL log.
        """
        if self._flatten_hook is not None and hasattr(
            self._flatten_hook, "fire_and_forget"
        ):
            self._flatten_hook.fire_and_forget(
                reason=f"disconnect_close_protect: {reason}"
            )
            logger.warning(
                "CLOSE_PROTECT triggered after WS grace expiry (reason=%s)",
                reason,
            )
        else:
            logger.critical(
                "CLOSE_PROTECT: WS grace expired without reconnect and no "
                "flatten_hook injected — manual intervention required "
                "(reason=%s)",
                reason,
            )

    async def _cancel_grace_timer(self) -> None:
        task = self._disconnect_grace_task
        self._disconnect_grace_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _rest_reconciliation_loop(self) -> None:
        """US-002: periodic REST reconciliation fallback.

        Every N seconds, pull recent orders from the exchange via
        get_all_orders. For any local order with status=ACCEPTED but
        exchange status=FILLED, call simulate_fill to update local
        state + trigger StopManager registration. Catches the case where
        WS user data stream dropped the fill event (proxy instability).
        """
        from selene_space_types.trading import OrderStatus

        # Track symbols we've seen orders for, so we poll only what's relevant.
        polled_symbols: set[str] = set()
        while True:
            try:
                await asyncio.sleep(self._rest_reconciliation_interval_s)

                # Discover symbols from local orders (skip if empty).
                local_symbols = {o.symbol for o in self._orders.values()}
                polled_symbols.update(local_symbols)
                if not polled_symbols:
                    continue

                for symbol in polled_symbols:
                    try:
                        raw_orders = await self._client.get_all_orders(  # type: ignore[attr-defined]
                            symbol=symbol, limit=50
                        )
                    except Exception as exc:
                        logger.warning(
                            "REST reconciliation: get_all_orders(%s) failed: %s",
                            symbol,
                            exc,
                        )
                        continue

                    for raw in raw_orders:
                        exchange_oid = str(raw.get("orderId", ""))
                        # Map exchange order ID → local internal ID
                        internal_id = self._exchange_to_internal.get(exchange_oid)
                        if internal_id is None:
                            # Also try client_order_id mapping
                            client_oid = raw.get("clientOrderId")
                            if client_oid:
                                internal_id = self._client_to_internal.get(client_oid)
                        if internal_id is None:
                            continue  # order not from this engine

                        local_order = self._orders.get(internal_id)
                        if local_order is None:
                            continue
                        if local_order.status == OrderStatus.FILLED:
                            continue  # already synced

                        ex_status = str(raw.get("status", "")).upper()
                        if ex_status != "FILLED":
                            continue

                        # Exchange says FILLED, local says otherwise → sync
                        fill_qty = Decimal(
                            str(raw.get("executedQty", 0) or local_order.quantity)
                        )
                        avg_px = Decimal(str(raw.get("avgPrice", 0) or 0.0))
                        logger.warning(
                            "REST reconciliation: order %s was FILLED on exchange but local status=%s, syncing",
                            internal_id,
                            local_order.status.value,
                        )
                        try:
                            await self.simulate_fill(
                                internal_id,
                                filled_quantity=fill_qty,
                                avg_price=avg_px if avg_px > 0 else None,
                            )
                        except Exception as exc:
                            logger.error(
                                "REST reconciliation: simulate_fill(%s) failed: %s",
                                internal_id,
                                exc,
                            )
            except asyncio.CancelledError:
                logger.info("REST reconciliation loop cancelled")
                raise
            except Exception as exc:
                # Loop must not die on unexpected errors.
                logger.warning("REST reconciliation loop error: %s", exc, exc_info=True)

    async def place_order(
        self,
        *,
        account_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        leverage: int = 1,
        margin_mode: Any = None,
        pos_side: Any = None,
        reduce_only: bool = False,
        time_in_force: str | None = None,
        metadata: dict[str, Any] | None = None,
        strategy_id: str | None = None,
        user_id: str | None = None,
    ) -> Order:
        # Phase 5.5-P0.5: SELENE_ENABLE_REAL_TRADING safety net.
        # Prevents accidental mainnet orders during dev/test. To send real
        # orders, set SELENE_ENABLE_REAL_TRADING=1 in the environment.
        # Paper mode (default) is exempt — paper never sends real orders.
        import os as _os

        if not _os.environ.get("SELENE_ENABLE_REAL_TRADING"):
            raise RuntimeError(
                "LiveExecutionEngine.place_order blocked: SELENE_ENABLE_REAL_TRADING not set. "
                "Set SELENE_ENABLE_REAL_TRADING=1 to allow real exchange orders (mainnet/testnet)."
            )

        # Phase 6: per-session first-trade approval gate. Even with
        # SELENE_ENABLE_REAL_TRADING set, the user must explicitly approve
        # this session via POST /api/v1/live/approve before any real order
        # is submitted. Resets on every engine restart.
        if not self._first_trade_approved:
            raise RuntimeError(
                "LiveExecutionEngine.place_order blocked: first-trade approval not granted for this session. "
                "Call POST /api/v1/live/approve to approve (frontend confirmation dialog). "
                "Approval resets on every engine restart."
            )

        # C3 fix: per-user approve gate (defense-in-depth). If user_id is
        # provided, it must be in _approved_users. Cross-user approve →
        # blocked here. If user_id is None (legacy caller), fall back to
        # the global flag check above (backward compat).
        if user_id is not None and user_id not in self._approved_users:
            raise RuntimeError(
                f"LiveExecutionEngine.place_order blocked: user {user_id} has not approved first-trade. "
                f"Call POST /api/v1/live/approve as user {user_id} first. "
                f"Approved users: {sorted(self._approved_users) or '(none)'}"
            )

        client_order_id = f"selenespace-{uuid.uuid4().hex[:12]}"
        internal_id = f"live-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)

        # Select the trading client: if routing is requested AND the router
        # + quote_provider + venues are all wired in, fetch quotes and route
        # to the best venue. Otherwise fall back to the default client.
        # Routing happens BEFORE the risk gate so routed orders still pass
        # pre_check (the gate runs against the engine's aggregated state,
        # not per-venue — this is intentional: the strategy's total exposure
        # is what matters for risk, not which venue fills it).
        client = self._client
        routed_exchange: str | None = None
        if (
            metadata is not None
            and metadata.get("route") is True
            and self._router is not None
            and self._quote_provider is not None
            and self._venues
        ):
            quotes = await self._quote_provider(symbol, side.value, quantity)
            if quotes:
                decision = self._router.route_order(quotes)
                venue_client = self._venues.get(decision.selected_exchange)
                if venue_client is not None:
                    client = venue_client
                    routed_exchange = decision.selected_exchange
                # else: fall back to default client (venue not registered).
            # else: no quotes available — fall back to default client.

        # 0. Pre-trade risk gate — block BEFORE touching journal or REST.
        #    Kill switch: hard block, no new orders when active.
        #    RiskEngine.pre_check: hard block on CRITICAL alerts.
        #    Both write a journal intent + mark_failed so there's an
        #    audit trail of the rejected attempt.
        #    P1-3: kill switch checks SYSTEM + ACCOUNT + STRATEGY scopes.
        #    strategy_id is caller-provided (defaults to None = unscoped).
        #    Bug F: reduce_only close orders bypass KillSwitch — closing an
        #    existing position cannot increase risk, and blocking stop-close
        #    causes unbounded loss when KillSwitch is the one that needs the
        #    close (e.g., stop_close_escalating_to_kill_switch path).
        if (
            not reduce_only
            and self._kill_switch is not None
            and self._kill_switch.is_active_for(
                account_id=account_id, strategy_id=strategy_id
            )
        ):
            if self._journal is not None:
                self._journal.append_intent(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side.value,
                    order_type=order_type.value,
                    quantity=quantity,
                    price=price,
                    account_id=account_id,
                    raw_request={
                        "internal_id": internal_id,
                        "stop_price": stop_price,
                        "routed_exchange": routed_exchange,
                        "rejected_by": "kill_switch",
                    },
                )
                self._journal.mark_failed(
                    client_order_id,
                    reason=f"kill_switch_active: {self._kill_switch.reason}",
                )
            raise PreTradeRiskBlockError(
                f"Order rejected: kill switch is active "
                f"({self._kill_switch.reason})",
                blocked_by="kill_switch",
                reason=self._kill_switch.reason,
            )

        # Phase 3: WS health gate — refuse to submit when market WS is degraded
        # (Binance 1008 rate limit / network partition). Placing orders during
        # WS blackout risks lost acks → phantom fills or duplicate orders on
        # reconnect. Gate sits AFTER kill_switch (kill_switch is a harder block
        # that always wins) but BEFORE risk_engine.pre_check (no point in
        # computing risk for an order we can't safely submit).
        if (
            self._ws_health_monitor is not None
            and not self._ws_health_monitor.is_healthy()
        ):
            ws_reason = self._ws_health_monitor.reason or "unknown"
            if self._journal is not None:
                self._journal.append_intent(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side.value,
                    order_type=order_type.value,
                    quantity=quantity,
                    price=price,
                    account_id=account_id,
                    raw_request={
                        "internal_id": internal_id,
                        "stop_price": stop_price,
                        "routed_exchange": routed_exchange,
                        "rejected_by": "ws_health_monitor",
                    },
                )
                self._journal.mark_failed(
                    client_order_id,
                    reason=f"ws_unhealthy: {ws_reason}",
                )
            from selene_space_exchange.execution.ws_health_monitor import (
                WsUnhealthyError,
            )

            raise WsUnhealthyError(ws_reason)

        if self._risk_engine is not None:
            from selene_space_types.agent import TradeIntent

            intent = TradeIntent(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                reasoning="live_engine.place_order",
                confidence=1.0,
                timestamp=ts,
                risk_params={"leverage": leverage},
            )
            # Assemble risk context. Field names match what rules.py expects:
            #   total_equity (float), positions (dict[symbol->notional]),
            #   portfolio_leverage (float), daily_realized_pnl (float),
            #   peak_equity (float), current_price (float).
            # NOTE: advanced_rules.py expects positions as dict[str, dict] —
            # mismatch is a known gap (P2), those rules silent-skip for now.
            # BalanceUpdate.free is Decimal (H19); risk-engine advisory math is
            # float-typed, so cast at this boundary to avoid float / Decimal TypeError.
            total_equity = float(
                sum((getattr(b, "free", 0.0) or 0.0) for b in self._balances)
            )
            positions_notional: dict[str, float] = {}
            for p in self._positions:
                try:
                    qty = float(p.quantity or 0.0)
                    px = float(p.avg_entry_price or 0.0)
                    notional = abs(qty) * px
                    positions_notional[p.symbol] = (
                        positions_notional.get(p.symbol, 0.0) + notional
                    )
                except Exception:
                    continue
            # Phase 2b: wire daily_realized_pnl + peak_equity from FillsLedger
            # and EquityTracker. When None (backward compat / shadow mode),
            # fall back to 0.0 / total_equity so MaxDailyLossRule and
            # DrawdownCircuitBreakerRule silent-skip (no false triggers).
            from datetime import datetime as _dt, timezone as _tz

            today_utc = _dt.now(_tz.utc).date()
            daily_realized_pnl = (
                self._fills_ledger.get_daily_realized_pnl(account_id, today_utc)
                if self._fills_ledger is not None
                else 0.0
            )
            peak_equity = (
                self._equity_tracker.peak
                if self._equity_tracker is not None
                else total_equity
            )

            context = {
                "account_id": account_id,
                "strategy_id": strategy_id,
                "leverage": leverage,
                "reduce_only": reduce_only,
                # rules.py MaxPositionRule expects positions as dict[symbol->notional].
                # advanced_rules expects dict[symbol->dict] — mismatch is a known
                # P2 gap; those rules silent-skip when .get() returns {}.
                "positions": positions_notional,
                "balances": list(self._balances),
                "total_equity": total_equity,
                "portfolio_leverage": float(leverage),
                "daily_realized_pnl": daily_realized_pnl,
                "peak_equity": peak_equity,
                "current_price": price if price is not None else 0.0,
                "order_tracker": self._order_tracker,
                "open_orders": [
                    o
                    for o in self._orders.values()
                    if o.status
                    not in (
                        OrderStatus.FILLED,
                        OrderStatus.CANCELLED,
                        OrderStatus.REJECTED,
                        OrderStatus.EXPIRED,
                    )
                ],
            }

            # Phase 2b: update EquityTracker after building context so peak
            # tracks total_equity for this tick. This is the post-context
            # update so the current decision uses the prior peak (correct:
            # drawdown is measured against historical peak, not current equity).
            if self._equity_tracker is not None:
                now_ms = int(time.time() * 1000)
                self._equity_tracker.update(total_equity, now_ms)

            decision = self._risk_engine.pre_check(intent, context)
            if not decision.allowed:
                if self._journal is not None:
                    self._journal.append_intent(
                        client_order_id=client_order_id,
                        symbol=symbol,
                        side=side.value,
                        order_type=order_type.value,
                        quantity=quantity,
                        price=price,
                        account_id=account_id,
                        raw_request={
                            "internal_id": internal_id,
                            "stop_price": stop_price,
                            "routed_exchange": routed_exchange,
                            "rejected_by": "risk_engine",
                            "risk_level": decision.level.value,
                            "risk_reason": decision.reason,
                        },
                    )
                    self._journal.mark_failed(
                        client_order_id,
                        reason=f"risk_blocked: {decision.reason}",
                    )
                raise PreTradeRiskBlockError(
                    f"Order rejected by risk engine "
                    f"(level={decision.level.value}): {decision.reason}",
                    blocked_by="risk_engine",
                    reason=decision.reason,
                )

        # 1. Write intent to journal BEFORE REST (crash-safe WAL).
        #    If we crash here, StartupReconciler will mark_intent_failed.
        if self._journal is not None:
            self._journal.append_intent(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side.value,
                order_type=order_type.value,
                quantity=quantity,
                price=price,
                account_id=account_id,
                raw_request={
                    "internal_id": internal_id,
                    "stop_price": stop_price,
                    "routed_exchange": routed_exchange,
                },
            )

        # Bug A fix: only STOP_MARKET / TAKE_PROFIT_MARKET orders need
        # stop_price on the exchange API. MARKET/LIMIT orders carry stop_price
        # for internal StopManager registration only — passing it to the
        # exchange triggers -1106 (parameter 'stopprice' sent when not
        # required).
        _exchange_stop_price = (
            stop_price
            if order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT)
            else None
        )

        request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)) if price is not None else None,
            stop_price=(
                Decimal(str(_exchange_stop_price))
                if _exchange_stop_price is not None
                else None
            ),
            time_in_force=time_in_force or "GTC",
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            pos_side=str(pos_side) if pos_side else None,
        )

        # 2. Send REST request via the selected client (default or routed).
        #    On failure, mark journal FAILED and re-raise.
        #    BinanceRESTClient has both kwargs-style place_order (legacy) and
        #    place_order_normalized(request) -> OrderResponse (TradingClient
        #    contract). Other exchanges (alpaca/qmt/solana) implement
        #    place_order(request) directly. Prefer the normalized method when
        #    available, else fall back to the unified place_order(request).
        try:
            if hasattr(client, "place_order_normalized"):
                response = await client.place_order_normalized(request)
            else:
                response = await client.place_order(request)
        except Exception as exc:
            if self._journal is not None:
                self._journal.mark_failed(client_order_id, reason=str(exc))
            raise

        # 3. REST succeeded — mark journal SUBMITTED and persist state.
        if self._journal is not None and response.order_id:
            self._journal.mark_submitted(client_order_id, response.order_id)

        order = Order(
            id=internal_id,
            symbol=symbol,
            side=side,
            type=order_type,
            status=response.status,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            filled_quantity=response.filled_quantity,
            avg_fill_price=response.avg_fill_price,
            reduce_only=reduce_only,
            created_at=ts,
            updated_at=ts,
        )

        self._orders[internal_id] = order
        self._client_to_internal[client_order_id] = internal_id
        if response.order_id:
            self._exchange_to_internal[response.order_id] = internal_id

        # Track for self-trade prevention (non-terminal orders only).
        if order.status not in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        ):
            from selene_space_risk.self_trade import ActiveOrder

            self._order_tracker.add(
                ActiveOrder(
                    order_id=internal_id,
                    symbol=symbol,
                    side=side,
                    strategy_id=strategy_id or "",
                    quantity=quantity,
                    price=price,
                )
            )

        # 4. Persist to LiveStateRepository (crash recovery source).
        if self._state_repo is not None:
            self._state_repo.save_order(
                order,
                client_order_id=client_order_id,
                exchange_order_id=response.order_id,
            )
            self._state_repo.save_id_mapping(
                internal_id,
                client_order_id=client_order_id,
                exchange_order_id=response.order_id,
            )

        await self._fire_order_update(order)

        # Bug C: register local stop loss after open fill (only when
        # StopManager injected + non-reduce_only + stop_price provided +
        # order actually FILLED). Trailing/buffer logic is caller's job.
        await self._register_stop_for_order(internal_id)

        return order

    async def _register_stop_for_order(self, internal_id: str) -> bool:
        """Register stop with StopManager if order is FILLED + has stop_price.

        Extracted from place_order (Step C3) so it can be reused by:
        - simulate_fill test endpoint (US-001)
        - REST reconciliation fallback (US-002)

        Returns True if a stop was registered, False otherwise.
        """
        order = self._orders.get(internal_id)
        if order is None:
            return False
        if (
            self._stop_manager is None
            or order.reduce_only
            or order.stop_price is None
            or order.status != OrderStatus.FILLED
        ):
            return False

        from selene_space_exchange.execution.stop_manager import StopSide

        stop_side = StopSide.LONG if order.side == OrderSide.BUY else StopSide.SHORT
        handle = self._stop_manager.register_stop(
            symbol=order.symbol,
            side=stop_side,
            stop_price=order.stop_price,
            quantity=order.filled_quantity or order.quantity,
            reduce_only=True,
            position_link=internal_id,
        )
        logger.info(
            "stop_registered: stop_id=%s symbol=%s side=%s stop_price=%s qty=%s position_link=%s",
            handle.stop_id,
            order.symbol,
            handle.side.value,
            handle.stop_price,
            handle.quantity,
            internal_id,
        )
        if self._state_repo is not None:
            self._state_repo.save_stops(self._stop_manager.list_active_stops())
        return True

    async def simulate_fill(
        self,
        order_id: str,
        *,
        filled_quantity: float | Decimal | None = None,
        avg_price: float | Decimal | None = None,
    ) -> Order:
        """Manually mark an order FILLED + trigger stop registration.

        Test-only endpoint for verifying StopManager trigger chain without
        relying on WS user data stream (which drops fill events when proxy
        is unstable). Gate via SELENE_ENABLE_TEST_ENDPOINTS env var at the
        API layer; this method itself does not check (engine-level).

        Used by POST /api/v1/live/test/simulate_fill (US-001) and by
        REST reconciliation fallback (US-002) when WS drops a fill.
        """
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")

        if order.status == OrderStatus.FILLED:
            logger.info("simulate_fill: order %s already FILLED, no-op", order_id)
            return order

        fill_qty = filled_quantity if filled_quantity is not None else order.quantity
        fill_px = avg_price if avg_price is not None else order.price or 0.0

        # Update in-memory + persist
        updated = order.model_copy(
            update={
                "status": OrderStatus.FILLED,
                "filled_quantity": fill_qty,
                "avg_fill_price": fill_px,
                "updated_at": int(time.time() * 1000),
            }
        )
        self._orders[order_id] = updated

        if self._state_repo is not None:
            self._state_repo.save_order(updated)
            ex_oid = self._find_exchange_order_id(order_id)
            self._state_repo.save_id_mapping(
                order_id,
                client_order_id=None,
                exchange_order_id=ex_oid,
            )

        await self._fire_order_update(updated)
        logger.info(
            "simulate_fill: order %s marked FILLED qty=%s avg_price=%s",
            order_id,
            fill_qty,
            fill_px,
        )

        # Trigger stop registration (same path as place_order post-fill)
        await self._register_stop_for_order(order_id)

        return updated

    async def cancel_order(self, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")

        exchange_oid = self._find_exchange_order_id(order_id)
        if exchange_oid:
            await self._client.cancel_order(symbol=order.symbol, order_id=exchange_oid)

        order = Order(
            id=order.id,
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            status=OrderStatus.CANCELLED,
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            filled_quantity=order.filled_quantity,
            avg_fill_price=order.avg_fill_price,
            created_at=order.created_at,
            updated_at=int(time.time() * 1000),
        )
        self._orders[order_id] = order

        # Remove from self-trade tracker (order is now terminal).
        self._order_tracker.remove(order_id)

        if self._state_repo is not None:
            self._state_repo.save_order(order)
        if self._journal is not None:
            co_id = ""
            for co, iid in self._client_to_internal.items():
                if iid == order_id:
                    co_id = co
                    break
            if co_id:
                self._journal.mark_confirmed(co_id)

        await self._fire_order_update(order)
        return order

    async def get_orders(
        self, account_id: str, *, status: OrderStatus | None = None
    ) -> list[Order]:
        orders = list(self._orders.values())
        if status is not None:
            orders = [o for o in orders if o.status == status]
        return orders

    async def get_open_orders(self, account_id: str) -> list[Order]:
        terminal = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
        return [o for o in self._orders.values() if o.status not in terminal]

    async def get_positions(self, account_id: str) -> list[Position]:
        return list(self._positions)

    async def get_balances(self, account_id: str) -> list[BalanceUpdate]:
        return list(self._balances)

    async def get_fills(self, account_id: str) -> list[Fill]:
        return list(self._fills)

    # ------------------------------------------------------------------
    # Event callback registration
    # ------------------------------------------------------------------

    def on_order_update(self, callback: Callable[[Order], Any]) -> None:
        self._on_order_update_cbs.append(callback)

    def on_fill(self, callback: Callable[[Fill], Any]) -> None:
        self._on_fill_cbs.append(callback)

    def on_position_update(self, callback: Callable[[Position], Any]) -> None:
        self._on_position_update_cbs.append(callback)

    def on_balance_update(self, callback: Callable[[list[BalanceUpdate]], Any]) -> None:
        self._on_balance_update_cbs.append(callback)

    # ------------------------------------------------------------------
    # WS event handlers — wire these to the private WS client
    # ------------------------------------------------------------------

    async def handle_exchange_order_update(self, update: ExchangeOrderUpdate) -> None:
        """Process a real-time order update from the exchange private WS.

        This is the PRIMARY path for order tracking.
        """
        internal_id = self._exchange_to_internal.get(
            update.exchange_order_id
        ) or self._client_to_internal.get(update.client_order_id or "")

        status_map = {
            "NEW": OrderStatus.ACCEPTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
            "PENDING_CANCEL": OrderStatus.PENDING_CANCEL,
            "PENDING_CANCELED": OrderStatus.PENDING_CANCEL,  # OKX spelling variant
            "TRIGGERED": OrderStatus.TRIGGERED,
        }

        if internal_id and internal_id in self._orders:
            order = self._orders[internal_id]
            order.status = status_map.get(update.status, order.status)
            order.filled_quantity = update.filled_quantity
            order.avg_fill_price = (
                update.avg_fill_price
                if update.avg_fill_price > 0
                else order.avg_fill_price
            )
            order.updated_at = update.timestamp
            await self._fire_order_update(order)

            # Persist updated order state and mark journal CONFIRMED on terminal states.
            if self._state_repo is not None:
                self._state_repo.save_order(order)
            if self._journal is not None and order.status in (
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
            ):
                # Resolve client_order_id from update or reverse-lookup via internal_id.
                co_id = update.client_order_id or ""
                if not co_id:
                    for co, iid in self._client_to_internal.items():
                        if iid == internal_id:
                            co_id = co
                            break
                if co_id:
                    self._journal.mark_confirmed(co_id)
            # Remove from self-trade tracker when order reaches terminal state.
            if order.status in (
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
            ):
                self._order_tracker.remove(internal_id)
        else:
            # P2-1: isolate external manual orders. A client_order_id that
            # does not match this system's prefixes was placed outside the
            # engine (manual UI / exchange app). Log a one-time warning and
            # drop it — the strategy engine must never act on orders it
            # doesn't own. Orders without a client_order_id keep the legacy
            # ext-tracking path (can't prove they're manual).
            if update.client_order_id and classify_unknown_order(
                update.client_order_id, self._client_order_prefixes
            ):
                if should_warn_outside_order():
                    logger.warning(
                        "Manual (outside) order detected and isolated: "
                        "client_order_id=%s symbol=%s status=%s",
                        update.client_order_id,
                        update.symbol,
                        update.status,
                    )
                return

            # Order from external source — track it
            internal_id = f"ext-{uuid.uuid4().hex[:8]}"
            order = Order(
                id=internal_id,
                symbol=update.symbol,
                side=update.side,
                type=self._map_order_type(update.order_type),
                status=status_map.get(update.status, OrderStatus.ACCEPTED),
                quantity=update.quantity,
                filled_quantity=update.filled_quantity,
                avg_fill_price=update.avg_fill_price
                if update.avg_fill_price > 0
                else None,
                created_at=update.timestamp,
                updated_at=update.timestamp,
            )
            self._orders[internal_id] = order
            self._exchange_to_internal[update.exchange_order_id] = internal_id
            if update.client_order_id:
                self._client_to_internal[update.client_order_id] = internal_id
            await self._fire_order_update(order)

        # Create fill if there was a new execution
        if update.last_fill_quantity > 0:
            fill = Fill(
                id=f"fill-{uuid.uuid4().hex[:8]}",
                order_id=internal_id,
                price=update.last_fill_price,
                quantity=update.last_fill_quantity,
                fee=update.commission,
                fee_currency=update.commission_asset or "USDT",
                timestamp=update.timestamp,
            )
            self._fills.append(fill)
            if self._state_repo is not None:
                self._state_repo.save_fill(fill)
            await self._fire_fill(fill)

    async def handle_balance_update(self, updates: list[BalanceUpdate]) -> None:
        """Process balance updates from the exchange private WS."""
        for upd in updates:
            existing = next((b for b in self._balances if b.asset == upd.asset), None)
            if existing:
                self._balances.remove(existing)
            self._balances.append(upd)
        if self._state_repo is not None:
            self._state_repo.save_balances(self._balances)
        await self._fire_balance_update(self._balances)

    async def handle_position_update(self, position: Position) -> None:
        """Process a position update from the exchange private WS.

        Updates internal position state and fires callbacks.
        Called when the exchange pushes position changes via the private
        WebSocket channel (e.g. on fill, funding, or liquidation).
        """
        existing_idx = None
        for idx, pos in enumerate(self._positions):
            if pos.symbol == position.symbol and pos.pos_side == position.pos_side:
                existing_idx = idx
                break

        if existing_idx is not None:
            self._positions[existing_idx] = position
        else:
            self._positions.append(position)

        if self._state_repo is not None:
            self._state_repo.save_positions(self._positions)
        await self._fire_position_update(position)

    async def handle_position_updates(self, positions: list[Position]) -> None:
        """Process batch position updates (WS push or REST reconciliation)."""
        for position in positions:
            await self.handle_position_update(position)

    # ------------------------------------------------------------------
    # Bug C: StopManager price tick + stop-close execution
    # ------------------------------------------------------------------

    async def on_price_tick(self, symbol: str, price: float) -> None:
        """Process a price tick — trigger any active stops for this symbol.

        Sync call to StopManager.on_price_tick (O(n) over active stops),
        then fire-and-forget asyncio.create_task for each triggered stop's
        reduce_only MARKET close. Does not block the tick loop.

        v3 BLOCK B fix: tasks held in self._pending_stop_tasks set to prevent
        GC cancellation. Cleared via done_callback when task completes.
        """
        if self._stop_manager is None:
            return
        triggered = self._stop_manager.on_price_tick(symbol, price)
        for t in triggered:
            handle = self._stop_manager.get_stop(t["stop_id"])
            if handle is None:
                continue
            logger.info(
                "stop_triggered: stop_id=%s symbol=%s side=%s stop_price=%s trigger_price=%s",
                handle.stop_id,
                symbol,
                handle.side.value,
                handle.stop_price,
                t["trigger_price"],
            )
            # v3 BLOCK B fix: hold strong ref to prevent GC canceling mid-flight
            task = asyncio.create_task(
                self._execute_stop_close(handle, t["trigger_price"])
            )
            self._pending_stop_tasks.add(task)
            task.add_done_callback(self._pending_stop_tasks.discard)

    async def _execute_stop_close(self, handle: Any, trigger_price: float) -> None:
        """Execute the reduce_only MARKET close for a triggered stop.

        3 retries with 50ms interval (total 150ms). On 3rd failure,
        escalate to kill_switch.activate() so the system-wide circuit
        breaker protects the position.
        """
        from selene_space_exchange.execution.stop_manager import StopSide

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                await self.place_order(
                    account_id="stop-close",
                    symbol=handle.symbol,
                    side=OrderSide.SELL
                    if handle.side == StopSide.LONG
                    else OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=handle.quantity,
                    reduce_only=True,
                    strategy_id=f"stop-{handle.stop_id}",
                )
                logger.info(
                    "stop_close_succeeded: stop_id=%s attempt=%d",
                    handle.stop_id,
                    attempt + 1,
                )
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "stop_close_attempt_failed: stop_id=%s attempt=%d error=%s",
                    handle.stop_id,
                    attempt + 1,
                    str(exc),
                )
                if attempt < 2:
                    await asyncio.sleep(0.05)  # 50ms
        # 3 次全失败 → 升级 KillSwitch
        logger.error(
            "stop_close_escalating_to_kill_switch: stop_id=%s trigger_price=%s last_error=%s",
            handle.stop_id,
            trigger_price,
            str(last_exc),
        )
        if self._kill_switch is not None:
            self._kill_switch.activate(reason=f"stop_trigger_failed: {handle.stop_id}")

    # ------------------------------------------------------------------
    # Reconciliation handlers
    # ------------------------------------------------------------------

    async def _handle_reconciliation_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._balances = list(snapshot.balances)
        await self._fire_balance_update(self._balances)

    async def _handle_reconciliation_orders(self, orders: list[OrderResponse]) -> None:
        for resp in orders:
            exchange_oid = resp.order_id
            if exchange_oid not in self._exchange_to_internal:
                internal_id = f"recon-{uuid.uuid4().hex[:8]}"
                order = Order(
                    id=internal_id,
                    symbol=resp.symbol,
                    side=resp.side,
                    type=resp.order_type,
                    status=resp.status,
                    quantity=resp.quantity,
                    filled_quantity=resp.filled_quantity,
                    price=resp.price,
                    avg_fill_price=resp.avg_fill_price,
                    created_at=resp.timestamp,
                    updated_at=resp.timestamp,
                )
                self._orders[internal_id] = order
                self._exchange_to_internal[exchange_oid] = internal_id
                if resp.client_order_id:
                    self._client_to_internal[resp.client_order_id] = internal_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_exchange_order_id(self, internal_id: str) -> str | None:
        for eid, iid in self._exchange_to_internal.items():
            if iid == internal_id:
                return eid
        return None

    @staticmethod
    def _map_order_type(exchange_type: str) -> OrderType:
        mapping = {
            "LIMIT": OrderType.LIMIT,
            "MARKET": OrderType.MARKET,
            "STOP_LOSS": OrderType.STOP_MARKET,
            "STOP_LOSS_LIMIT": OrderType.STOP_LIMIT,
            "limit": OrderType.LIMIT,
            "market": OrderType.MARKET,
        }
        return mapping.get(exchange_type, OrderType.MARKET)

    # ------------------------------------------------------------------
    # Event firing
    # ------------------------------------------------------------------

    async def _fire_order_update(self, order: Order) -> None:
        for cb in self._on_order_update_cbs:
            await self._invoke(cb, order)

    async def _fire_fill(self, fill: Fill) -> None:
        for cb in self._on_fill_cbs:
            await self._invoke(cb, fill)

    async def _fire_position_update(self, position: Position) -> None:
        for cb in self._on_position_update_cbs:
            await self._invoke(cb, position)

    async def _fire_balance_update(self, balances: list[BalanceUpdate]) -> None:
        for cb in self._on_balance_update_cbs:
            await self._invoke(cb, balances)

    @staticmethod
    async def _invoke(callback: Callable[..., Any], *args: Any) -> None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result
