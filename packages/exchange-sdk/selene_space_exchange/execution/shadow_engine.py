"""Shadow execution engine — dry-run live trading without real orders.

Implements the ExecutionEngine protocol but intercepts REST calls,
simulating fills with realistic slippage instead. All state is
persisted via OrderJournal and LiveStateRepository for crash recovery.

Use this to validate the full live trading pipeline before committing
real funds.
"""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Callable

from selene_space_types.trading import (
    BalanceUpdate,
    Fill,
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)

from selene_space_paper.fills import FixedPaperSlippage, PaperSlippageModel, RealisticSlippage, try_fill
from selene_space_paper.orders import PaperOrderManager

from .live_state import LiveStateRepository
from .order_journal import OrderJournal

logger = logging.getLogger(__name__)

_DEFAULT_INITIAL_BALANCE = 10_000.0


class _EquityLedger:
    """Single-stream equity bookkeeping for shadow vs paper-mirror comparison.

    Tracks cash balance + net position per symbol, revalued at the latest
    mark price. The shadow engine keeps two ledgers fed from the *same* fills
    but different slippage models (shadow uses RealisticSlippage, the mirror
    uses FixedPaperSlippage) so the equity deviation isolates slippage-model
    difference — exactly what Stage B's "<5% deviation → investigate
    RealisticSlippage" diagnosis expects.
    """

    def __init__(self, starting_balance: float = _DEFAULT_INITIAL_BALANCE) -> None:
        self.balance = starting_balance
        self.position: dict[str, float] = {}  # symbol → net qty (buy +, sell −)
        self._last_px: dict[str, float] = {}

    def apply_fill(self, *, symbol: str, side: OrderSide, price: float, quantity: float, fee: float = 0.0) -> None:
        """Apply a fill at the given price. Positive qty for buys, negative for sells."""
        signed_qty = quantity if side == OrderSide.BUY else -quantity
        self.balance -= price * signed_qty + fee
        self.position[symbol] = self.position.get(symbol, 0.0) + signed_qty
        self._last_px[symbol] = price

    def update_price(self, symbol: str, price: float) -> None:
        self._last_px[symbol] = price

    def equity(self) -> float:
        total = self.balance
        for sym, qty in self.position.items():
            total += qty * self._last_px.get(sym, 0.0)
        return total


class ShadowExecutionEngine:
    """Dry-run engine that mirrors LiveExecutionEngine logic without real API calls.

    Executes the full pipeline:
      1. Pre-trade risk check (optional)
      2. Write to OrderJournal (intent)
      3. Simulate REST response (fake OrderResponse)
      4. Simulate WS updates with realistic slippage
      5. Write to OrderJournal (confirmed)
      6. Persist state via LiveStateRepository

    No real API keys needed. No real orders placed.
    """

    # ShadowEngine accepts mark_price/signal_timestamp_ms in place_order
    # signature (journals them for observability). Flag reflects this
    # capability so StrategyRunner picks the right place_order signature.
    supports_mark_price: bool = True

    def __init__(
        self,
        *,
        journal: OrderJournal | None = None,
        state_repo: LiveStateRepository | None = None,
        slippage_model: PaperSlippageModel | None = None,
        simulate_latency_ms: tuple[int, int] = (50, 200),
        fill_on_next_tick: bool = True,
        paper_slippage: PaperSlippageModel | None = None,
        initial_balance: float = _DEFAULT_INITIAL_BALANCE,
    ) -> None:
        self._journal = journal or OrderJournal()
        self._state = state_repo or LiveStateRepository()
        self._slippage = slippage_model or RealisticSlippage()
        self._paper_slippage = paper_slippage or FixedPaperSlippage()
        self._latency_range = simulate_latency_ms
        self._fill_on_next_tick = fill_on_next_tick

        # Shadow vs paper-mirror ledgers — fed by the same fills at different
        # slippage prices. equity_deviation_pct() compares their equity.
        self._shadow_ledger = _EquityLedger(initial_balance)
        self._paper_ledger = _EquityLedger(initial_balance)

        self._order_manager = PaperOrderManager()
        self._accounts: dict[str, dict] = {}  # account_id → {balance, equity, ...}
        # Fill history — appended in _simulate_fill / on_price_tick. Queried
        # by get_fills(). Without this, get_fills returned [] silently,
        # making shadow dry-run verification look like zero fills happened.
        self._fills: list = []

        # Phase 5.5-P0: Observability layer — mirror LiveExecutionEngine pattern.
        # When None, downstream reads from live.py fall back gracefully (no crash).
        self._fills_ledger: Any | None = None
        self._equity_tracker: Any | None = None

        # Ticker message counter for ws_messages_30min health indicator.
        # Incremented on every on_price_tick call; last_msg_ts tracks freshness.
        self._ticker_msg_count: int = 0
        self._last_msg_ts: int | None = None

        # Strategy signals counter — incremented in place_order().
        self._strategy_signals_fired: int = 0

        # Callbacks (same interface as ExecutionEngine)
        self._on_order_update_cbs: list[Callable[[Order], Any]] = []
        self._on_fill_cbs: list[Callable[[Fill], Any]] = []
        self._on_position_update_cbs: list[Callable[[Position], Any]] = []
        self._on_balance_update_cbs: list[Callable[[list[BalanceUpdate]], Any]] = []

        # Pending market orders waiting for the next price tick to fill.
        # Keyed by symbol to allow on_price_tick to dispatch efficiently.
        # Real live semantics: a market order is submitted, then fills when
        # the next real tick arrives — not instantly on submission.
        self._pending_market_orders: dict[str, list[Order]] = {}

    @property
    def mode(self) -> str:
        return "shadow"

    @property
    def exchange(self) -> str:
        return "shadow"

    # -- Phase 5.5-P0: Observability setters/properties --

    def set_fills_ledger(self, fills_ledger: Any | None) -> None:
        """Inject the FillsLedger after construction (Phase 2b mirror)."""
        self._fills_ledger = fills_ledger

    def set_equity_tracker(self, equity_tracker: Any | None) -> None:
        """Inject the EquityTracker after construction (Phase 2b mirror)."""
        self._equity_tracker = equity_tracker

    def set_kill_switch(self, kill_switch: Any | None) -> None:
        """Inject the KillSwitch after construction (Phase 2 parity with LiveEngine).

        Also registers a cancel-all callback so that when the KillSwitch
        activates, every open shadow order (pending market queue + resting
        limit/stop) is cancelled and journaled as failed(cancelled_by_kill_switch).
        """
        self._kill_switch = kill_switch
        if kill_switch is not None:
            try:
                kill_switch.on_activate(self.cancel_all_open_orders)
            except Exception:
                logger.exception("Failed to register kill-switch cancel callback on shadow engine")

    @property
    def kill_switch(self) -> Any | None:
        """The wired KillSwitch instance, or None if not set."""
        return getattr(self, "_kill_switch", None)

    @property
    def ticker_msg_count_30min(self) -> int:
        """Ticker messages received in the last 30 minutes.

        If no messages received OR last message is older than 30 min, returns 0.
        """
        if self._last_msg_ts is None:
            return 0
        cutoff = (time.time() - 1800) * 1000  # 30 min ago in ms
        if self._last_msg_ts < cutoff:
            return 0
        return self._ticker_msg_count

    @property
    def equity_tracker_peak(self) -> float | None:
        """Peak equity from EquityTracker, or None if not wired."""
        if self._equity_tracker is None:
            return None
        return self._equity_tracker.peak

    @property
    def shadow_equity(self) -> float:
        """Current shadow-ledger equity (realistic slippage stream)."""
        return self._shadow_ledger.equity()

    @property
    def paper_mirror_equity(self) -> float:
        """Current paper-mirror-ledger equity (fixed 1bp slippage stream)."""
        return self._paper_ledger.equity()

    @property
    def equity_deviation_pct(self) -> float | None:
        """|shadow_equity − paper_mirror_equity| / paper_mirror_equity × 100.

        Returns None only when the paper mirror has zero equity baseline.
        With no fills both ledgers start identical → 0.0 (passes Stage B < 5%).
        Stage B criterion 5 threshold: < 5% passes.
        """
        paper_eq = self._paper_ledger.equity()
        if paper_eq == 0:
            return None
        return abs(self._shadow_ledger.equity() - paper_eq) / paper_eq * 100.0

    @property
    def strategy_signals_fired(self) -> int:
        """Number of strategy signals (place_order calls) since engine start."""
        return self._strategy_signals_fired

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info("ShadowExecutionEngine started")

    async def stop(self) -> None:
        logger.info("ShadowExecutionEngine stopped")

    # -- Order Operations --

    async def place_order(
        self,
        account_id: str,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        leverage: int = 1,
        margin_mode: MarginMode = MarginMode.CROSS,
        pos_side: PositionSide = PositionSide.NET,
        reduce_only: bool = False,
        mark_price: float | None = None,
        signal_timestamp_ms: int | None = None,
        client_order_id: str | None = None,
        strategy_id: str | None = None,
    ) -> Order:
        """Place a shadow order — writes to journal, simulates fill."""
        # Phase 5.5-P0: Track strategy signals fired for observability.
        self._strategy_signals_fired += 1
        # Initialize the account snapshot on first order so get_balances()
        # returns a real balance for traded accounts (ledger is authoritative
        # for equity; this dict mirrors its starting balance).
        self._accounts.setdefault(
            account_id,
            {"balance": self._shadow_ledger.balance, "equity": self._shadow_ledger.equity()},
        )
        # Caller-supplied client_order_id wins (e.g. StrategyRunner's
        # "{deployment_id}-{ts}"); otherwise generate the canonical sso- prefix.
        if not client_order_id:
            client_order_id = f"sso-{uuid.uuid4().hex[:8]}"
        elif not client_order_id.startswith("sso-"):
            # Normalize runner-supplied IDs so shadow entries remain grep-able
            # in the journal by the sso- prefix.
            client_order_id = f"sso-{client_order_id}"

        # Caller may pass side/order_type/margin_mode/pos_side as strings
        # (StrategyRunner does: `side.value if hasattr(side, "value") else str(side)`,
        # `margin_mode="cross"`, etc.). Coerce to the enums so downstream
        # `.value` access works.
        if isinstance(side, str):
            side = OrderSide(side)
        if isinstance(order_type, str):
            order_type = OrderType(order_type)
        if isinstance(margin_mode, str):
            margin_mode = MarginMode(margin_mode)
        if isinstance(pos_side, str):
            pos_side = PositionSide(pos_side)

        # 1. Write intent to journal
        self._journal.append_intent(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side.value,
            order_type=order_type.value,
            quantity=quantity,
            price=price,
            account_id=account_id,
            raw_request={
                "account_id": account_id,
                "symbol": symbol,
                "side": side.value,
                "order_type": order_type.value,
                "quantity": quantity,
                "price": price,
                "stop_price": stop_price,
                "leverage": leverage,
                "margin_mode": margin_mode.value,
                "pos_side": pos_side.value,
                "reduce_only": reduce_only,
                "mark_price": mark_price,
                "signal_timestamp_ms": signal_timestamp_ms,
                "strategy_id": strategy_id,
            },
        )

        # 2. Create order via PaperOrderManager
        order = self._order_manager.place_order(
            account_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            leverage=leverage,
            margin_mode=margin_mode,
            pos_side=pos_side,
            reduce_only=reduce_only,
        )
        # Stamp the client_order_id onto the Order so on_price_tick's
        # mark_confirmed call can find the journal row (mark_confirmed keys
        # on client_order_id, not exchange order.id).
        order.client_order_id = client_order_id

        # 3. Mark as submitted
        self._journal.mark_submitted(client_order_id, exchange_order_id=order.id)

        # 4. Save to persistent state
        self._state.save_order(order, client_order_id=client_order_id, exchange_order_id=order.id)
        self._state.save_id_mapping(order.id, client_order_id=client_order_id, exchange_order_id=order.id)

        # 5. Fire callbacks
        self._fire_order_update(order)

        # 6. For market orders, enqueue for fill on next price tick.
        # Real exchange semantics: a market order rests in the book until
        # matched against incoming market data. We never fabricate a fill
        # price out of thin air (the old `1.0` placeholder was wrong).
        # If the caller supplied mark_price (e.g. from the live ticker), use
        # it to fill immediately — matches PaperExecutionEngine behavior and
        # guarantees a fill when no WS tick stream is available.
        if order_type == OrderType.MARKET:
            if mark_price is not None and mark_price > 0:
                self._pending_market_orders.setdefault(order.symbol, []).append(order)
                await self.on_price_tick(order.symbol, mark_price)
            elif self._fill_on_next_tick:
                self._pending_market_orders.setdefault(order.symbol, []).append(order)

        return order

    async def cancel_order(self, order_id: str) -> Order:
        order = self._order_manager.cancel_order(order_id)
        self._state.save_order(order)
        self._fire_order_update(order)
        return order

    def cancel_all_open_orders(self) -> int:
        """Cancel every open shadow order — pending market queue + resting orders.

        Synchronous (no real exchange calls in shadow mode) so it can be
        registered directly as a KillSwitch activation callback. Each cancelled
        order is journaled as failed(reason=cancelled_by_kill_switch) so the WAL
        never leaves stale intent/submitted entries (verdict check_2). Returns
        the number of orders cancelled.
        """
        cancelled = 0
        # 1. Pending market orders awaiting the next tick.
        for symbol in list(self._pending_market_orders):
            queue = self._pending_market_orders.pop(symbol, [])
            for order in queue:
                cancelled += self._cancel_one(order, reason="cancelled_by_kill_switch")
        # 2. Resting limit/stop orders.
        for order in self._order_manager.get_open_orders():
            cancelled += self._cancel_one(order, reason="cancelled_by_kill_switch")
        logger.info("Shadow kill-switch cancel: %d open order(s) cancelled", cancelled)
        return cancelled

    def _cancel_one(self, order: Order, *, reason: str) -> int:
        """Cancel a single order, persist, journal, notify. Returns 1 on success."""
        try:
            updated = self._order_manager.cancel_order(order.id)
        except Exception:
            logger.exception("Shadow cancel failed for order %s", order.id)
            return 0
        self._journal.mark_failed(order.client_order_id or order.id, reason=reason)
        self._state.save_order(updated)
        self._fire_order_update(updated)
        return 1

    async def get_orders(self, account_id: str, status: OrderStatus | None = None) -> list[Order]:
        return self._order_manager.get_orders(account_id, status)

    async def get_open_orders(self, account_id: str) -> list[Order]:
        return self._order_manager.get_open_orders(account_id)

    async def get_positions(self, account_id: str) -> list[Position]:
        """Return open positions for the account.

        Shadow engine does not track a separate position ledger — positions
        are derived from the order/fill history in ``self._fills``. For a
        full dry-run P&L report, query ``get_fills`` and aggregate by
        symbol. Returns an empty list here because Position requires
        unrealized PnL which shadow cannot compute without a live mark
        price source.
        """
        return []

    async def get_balances(self, account_id: str) -> list[BalanceUpdate]:
        """Return balance snapshots for the account.

        Shadow engine initializes ``self._accounts`` lazily on first
        place_order call. Returns the cached balance dict entries as
        BalanceUpdate objects. Empty list if the account has never traded.
        """
        acct = self._accounts.get(account_id)
        if not acct:
            return []
        balance = float(acct.get("balance", 0.0))
        return [
            BalanceUpdate(
                exchange="shadow",
                asset="USDT",
                free=balance,
                locked=0.0,
                timestamp=int(time.time() * 1000),
            )
        ]

    async def get_fills(self, account_id: str) -> list[Fill]:
        """Return fills for the account, filtered by symbol ownership.

        Shadow engine stores all fills in ``self._fills`` (appended in
        ``_fire_fill``). Filter by account_id where Fill exposes it;
        otherwise return all (shadow runs are single-account by design).
        """
        # Fill doesn't carry account_id directly — shadow engine is
        # single-account per instance, so all fills belong to the caller.
        # If account_id doesn't match any known account, return [].
        if account_id and account_id not in self._accounts and self._accounts:
            return []
        return list(self._fills)

    # -- Callbacks --

    def on_order_update(self, callback: Callable[[Order], Any]) -> None:
        self._on_order_update_cbs.append(callback)

    def on_fill(self, callback: Callable[[Fill], Any]) -> None:
        self._on_fill_cbs.append(callback)

    def on_position_update(self, callback: Callable[[Position], Any]) -> None:
        self._on_position_update_cbs.append(callback)

    def on_balance_update(self, callback: Callable[[list[BalanceUpdate]], Any]) -> None:
        self._on_balance_update_cbs.append(callback)

    def on_position_update_scoped(self, callback: Callable[[str, Position], Any]) -> None:
        self._on_position_update_cbs.append(lambda p: callback(account_id_from_position(p), p))

    def on_balance_update_scoped(self, callback: Callable[[str, list[BalanceUpdate]], Any]) -> None:
        self._on_balance_update_cbs.append(lambda b: callback("shadow", b))

    # -- Price tick integration --

    async def on_price_tick(self, symbol: str, price: float) -> None:
        """Process a price tick — fill pending market orders and trigger limit/stop orders."""
        # Phase 5.5-P0: Ticker message counter for ws_messages_30min health indicator.
        self._ticker_msg_count += 1
        self._last_msg_ts = int(time.time() * 1000)
        # 1. Drain pending market orders using the real tick price.
        pending = self._pending_market_orders.pop(symbol, [])
        for order in pending:
            if order.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIAL):
                continue
            fill_price = self._slippage.apply(
                price,
                order.side,
                order_notional=order.quantity,
            )
            remaining = order.quantity - order.filled_quantity
            fill_price_dec = fill_price if fill_price > 0 else Decimal(str(price))
            fill = Fill(
                id=f"fill-{uuid.uuid4().hex[:8]}",
                order_id=order.id,
                price=fill_price_dec,
                quantity=remaining,
                fee=remaining * Decimal("0.0005"),
                fee_currency="USDT",
                fee_rate=Decimal("0.0005"),
                exec_type="taker",
                side=order.side.value,
                pos_side=order.pos_side.value,
                symbol=order.symbol,
                leverage=order.leverage,
                timestamp=int(time.time() * 1000),
            )
            updated = self._order_manager.update_fill(order.id, fill.quantity, fill.price)
            self._journal.mark_confirmed(order.client_order_id or order.id)
            self._state.save_order(updated)
            self._state.save_fill(fill)
            self._apply_fill_to_ledgers(
                symbol=order.symbol,
                side=order.side,
                shadow_price=float(fill_price_dec),
                paper_price=self._paper_slippage.apply(price, order.side, order_notional=order.quantity),
                quantity=float(remaining),
                fee=float(fill.fee) if fill.fee is not None else 0.0,
            )
            self._fire_fill(fill)
            self._fire_order_update(updated)
            # Phase 5.5-P0: Feed data to FillsLedger/EquityTracker so observability has real data.
            if self._fills_ledger is not None:
                self._fills_ledger.record_fill(fill, account_id="shadow")
            if self._equity_tracker is not None:
                self._equity_tracker.update(self._shadow_ledger.equity(), fill.timestamp)

        # 2. Process limit/stop orders against the tick (existing behavior).
        open_orders = self._order_manager.get_open_orders()
        for order in open_orders:
            if order.symbol != symbol:
                continue
            # Defensive guard: get_open_orders() may include orders that
            # try_fill has already transitioned to FILLED via a prior tick
            # in this same on_price_tick call (e.g. when the same order
            # appears twice due to dict/list aliasing). update_fill raises
            # InvalidOrderTransition on such orders, which crashes the
            # on_price_tick task and leaves journal entries stuck in
            # 'intent' state.
            if order.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIAL):
                continue
            fill = try_fill(order, price, slippage_model=self._slippage)
            if fill is not None:
                # try_fill mutates the Order in-place (filled_quantity, status,
                # avg_fill_price). Calling update_fill here re-applies the
                # qty delta and raises InvalidOrderTransition on orders that
                # try_fill has already transitioned to FILLED. Use sync_fill
                # to update timestamp + tracking without re-applying state.
                updated = self._order_manager.sync_fill(order.id)
                self._apply_fill_to_ledgers(
                    symbol=order.symbol,
                    side=order.side,
                    shadow_price=float(fill.price),
                    paper_price=self._paper_slippage.apply(price, order.side, order_notional=order.quantity),
                    quantity=float(fill.quantity),
                    fee=float(fill.fee) if fill.fee is not None else 0.0,
                )
                self._fire_fill(fill)
                self._fire_order_update(updated)
                # Phase 5.5-P0: Feed data to FillsLedger/EquityTracker so observability has real data.
                if self._fills_ledger is not None:
                    self._fills_ledger.record_fill(fill, account_id="shadow")
                if self._equity_tracker is not None:
                    self._equity_tracker.update(self._shadow_ledger.equity(), fill.timestamp)

                if updated.status == OrderStatus.FILLED:
                    self._journal.mark_confirmed(order.id)
                self._state.save_order(updated)
                self._state.save_fill(fill)

        # Mark both ledgers at the SAME tick price so the position leg cancels
        # out of the equity comparison — otherwise each ledger marks its own
        # fill price and the slippage cost is masked (balance + qty*own_fill_px
        # ≈ starting balance − fee regardless of fill price). With a shared
        # mark, equity difference = pure execution-cost (slippage) difference.
        self._shadow_ledger.update_price(symbol, price)
        self._paper_ledger.update_price(symbol, price)

    # -- Internal --

    def _apply_fill_to_ledgers(
        self,
        *,
        symbol: str,
        side: OrderSide,
        shadow_price: float,
        paper_price: float | Decimal,
        quantity: float,
        fee: float,
    ) -> None:
        """Feed one fill into both ledgers at their own slippage-adjusted prices.

        shadow ledger uses the shadow fill price (RealisticSlippage), the
        paper-mirror uses the same tick through FixedPaperSlippage. Both also
        get the latest mark price so open-position equity revalues correctly.
        """
        self._shadow_ledger.apply_fill(
            symbol=symbol, side=side, price=float(shadow_price), quantity=quantity, fee=fee,
        )
        self._paper_ledger.apply_fill(
            symbol=symbol, side=side, price=float(paper_price), quantity=quantity, fee=fee,
        )
        self._shadow_ledger.update_price(symbol, float(shadow_price))
        self._paper_ledger.update_price(symbol, float(paper_price))
        # Persist the comparison point so verdict.py (separate process) can
        # verify Stage B criterion 5 (|shadow − paper| / paper < 5%) across
        # the whole run. Non-fatal: observability loss should never crash fills.
        try:
            self._state.save_equity_snapshot(
                shadow_equity=self._shadow_ledger.equity(),
                paper_mirror_equity=self._paper_ledger.equity(),
                deviation_pct=self.equity_deviation_pct,
            )
        except Exception:
            logger.exception("Failed to persist shadow/paper equity snapshot")

    def _fire_order_update(self, order: Order) -> None:
        for cb in self._on_order_update_cbs:
            try:
                cb(order)
            except Exception:
                logger.exception("Shadow order update callback failed")

    def _fire_fill(self, fill: Fill) -> None:
        # Track fill for get_fills() queries — without this, dry-run
        # verification looked like zero fills happened even though fills
        # were fired via callbacks. State repo also saves the fill, but
        # get_fills reads from this in-memory list for O(1) access.
        self._fills.append(fill)
        for cb in self._on_fill_cbs:
            try:
                cb(fill)
            except Exception:
                logger.exception("Shadow fill callback failed")


def account_id_from_position(pos: Position) -> str:
    return "shadow"
