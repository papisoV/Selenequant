"""PaperExecutionEngine — async paper backend mirroring the live-engine surface.

Concurrency model:
  - One ``asyncio.Lock`` per account serializes every fill/cancel pipeline
    (H1: no double fill; H8: cancel vs fill stays consistent).
  - ``place_order`` runs the market-fill path *inside* the same lock via the
    ``_locked`` inner routine, so it can never deadlock against a tick fill.
  - Any mid-fill exception rolls back order / account / position mutations
    before re-raising (H3).
  - ``delete_account_async`` cancels in-flight fill tasks for the account
    before removing it (H14). CancelledError always releases locks because
    acquisition uses ``async with``.

Risk gate is dormant unless BOTH an engine was injected and the env flag
``SELENE_PAPER_RISK_ENFORCE`` is truthy — paper stays friction-free by default.
"""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal
from typing import Any, Callable

from selene_space_paper.accounts import AccountManager, PaperAccount
from selene_space_paper.fills import (
    FixedPaperSlippage,
    PaperSlippageModel,
    try_fill,
)
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.positions import PositionManager
from selene_space_types.trading import (
    ExecutionMode,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

DEFAULT_ACCOUNT_ID = "paper-default"
DEFAULT_INITIAL_BALANCE = 100000.0

_TRUTHY = {"1", "true", "yes", "on"}


def _paper_risk_enforce_enabled() -> bool:
    """Read at call time so tests and operators can flip it without restart."""
    raw = os.environ.get("SELENE_PAPER_RISK_ENFORCE", "")
    return raw.strip().lower() in _TRUTHY


class OrderRejected(Exception):
    """Raised when the optional risk gate denies an order pre-placement."""


def _ms() -> int:
    return int(time.time() * 1000)


class PaperExecutionEngine:
    """Simulated exchange: instant MARKET fills, tick-triggered LIMIT fills."""

    supports_mark_price: bool = True

    def __init__(
        self,
        *,
        default_account_id: str = DEFAULT_ACCOUNT_ID,
        initial_balance: float = DEFAULT_INITIAL_BALANCE,
        fee_rate: float = 0.001,
        risk_engine: Any | None = None,
    ) -> None:
        self._default_account_id = default_account_id
        self._initial_balance = float(initial_balance)
        self._fee_rate = float(fee_rate)
        self._risk_engine = risk_engine
        self._account_mgr = AccountManager()
        self._order_mgr = PaperOrderManager()
        self._position_mgr = PositionManager()
        self._fills: list[Fill] = []
        self._slippage: PaperSlippageModel = FixedPaperSlippage(bps=1)
        self._last_price: dict[str, float] = {}
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._fill_tasks: set[asyncio.Task[Any]] = set()
        self._running = False
        self._cbs_order: list[Callable[[Order], Any]] = []
        self._cbs_fill: list[Callable[[Fill], Any]] = []
        self._cbs_position: list[Callable[[Any], Any]] = []
        self._cbs_balance: list[Callable[[list[Any]], Any]] = []

    # ------------------------------------------------------------------
    # Identity & lifecycle
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.PAPER

    @property
    def exchange(self) -> str:
        return "paper"

    async def start(self) -> None:
        if not self._account_mgr.get_account(self._default_account_id):
            self._ensure_account(self._default_account_id)
        self._running = True

    async def stop(self) -> None:
        self._running = False
        pending = [t for t in self._fill_tasks if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._fill_tasks.clear()

    # ------------------------------------------------------------------
    # Accounts & fees
    # ------------------------------------------------------------------

    def _ensure_account(self, account_id: str) -> PaperAccount:
        account = self._account_mgr.get_account(account_id)
        if account is None:
            account = self._account_mgr.create_account(
                account_id, self._initial_balance, account_id=account_id
            )
        return account

    def _get_fee_rates(self, account_id: str) -> tuple[float, float]:
        """(maker, taker); fall back to the flat engine rate when unset."""
        account = self._account_mgr.get_account(account_id)
        if account is None:
            return (self._fee_rate, self._fee_rate)
        maker = getattr(account, "maker_fee_rate", None)
        taker = getattr(account, "taker_fee_rate", None)
        if maker is None or taker is None or maker <= 0 or taker <= 0:
            return (self._fee_rate, self._fee_rate)
        return (float(maker), float(taker))

    def update_fee_rates(self, account_id: str, maker: float, taker: float) -> None:
        account = self._ensure_account(account_id)
        account.maker_fee_rate = float(maker)
        account.taker_fee_rate = float(taker)

    def compute_equity(self, account_id: str) -> float:
        account = self._account_mgr.get_account(account_id)
        wallet = float(account.current_balance) if account else 0.0
        unrealized = sum(
            float(p.unrealized_pnl) for p in self._position_mgr.get_open_positions(account_id)
        )
        return wallet + unrealized

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_orders(
        self, account_id: str, *, status: OrderStatus | None = None
    ) -> list[Order]:
        return self._order_mgr.get_orders(account_id, status=status)

    async def get_open_orders(self, account_id: str) -> list[Order]:
        return self._order_mgr.get_open_orders(account_id)

    async def get_positions(self, account_id: str) -> list[Any]:
        return [
            p
            for p in self._position_mgr.get_positions(account_id)
            if p.quantity > 0
        ]

    async def get_balances(self, account_id: str) -> list[Any]:
        from selene_space_types.trading import BalanceUpdate

        account = self._account_mgr.get_account(account_id)
        free = float(account.current_balance) if account else 0.0
        return [
            BalanceUpdate(
                exchange="paper",
                asset="USDT",
                free=free,
                locked=0.0,
                timestamp=_ms(),
            )
        ]

    async def get_fills(self, account_id: str) -> list[Fill]:
        owned = {
            oid
            for oid in getattr(self._order_mgr, "_owner", {})
            if getattr(self._order_mgr, "_owner", {}).get(oid) == account_id
        }
        return [f for f in self._fills if f.order_id in owned]

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

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
        margin_mode: Any = None,
        pos_side: Any = None,
        reduce_only: bool = False,
        mark_price: float | None = None,
        signal_timestamp_ms: int | None = None,
        client_order_id: str | None = None,
        strategy_id: str | None = None,
        time_in_force: str | None = None,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> Order:
        return await self._place_order_inner(
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
            mark_price=mark_price,
            signal_timestamp_ms=signal_timestamp_ms,
            client_order_id=client_order_id,
            strategy_id=strategy_id,
            time_in_force=time_in_force,
            tp_price=tp_price,
            sl_price=sl_price,
        )

    async def _place_order_inner(
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
        margin_mode: Any = None,
        pos_side: Any = None,
        reduce_only: bool = False,
        mark_price: float | None = None,
        signal_timestamp_ms: int | None = None,
        client_order_id: str | None = None,
        strategy_id: str | None = None,
        time_in_force: str | None = None,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> Order:
        self._ensure_account(account_id)

        await self._risk_gate(account_id, symbol, side, quantity, mark_price)

        resolve_px = mark_price if mark_price is not None else self._last_price.get(symbol)
        if resolve_px is None and order_type == OrderType.MARKET:
            raise ValueError(f"No reference price for {symbol}; feed a tick first")

        lock = self._lock_for(account_id)
        async with lock:
            from selene_space_types.trading import MarginMode as _MM
            from selene_space_types.trading import PositionSide as _PS

            order = self._order_mgr.place_order(
                account_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                leverage=leverage,
                margin_mode=margin_mode if margin_mode is not None else _MM.CROSS,
                pos_side=pos_side if pos_side is not None else _PS.NET,
                reduce_only=reduce_only,
                client_order_id=client_order_id,
                strategy_id=strategy_id,
                signal_timestamp_ms=signal_timestamp_ms,
                time_in_force=time_in_force,
                tp_price=tp_price,
                sl_price=sl_price,
            )

            if order_type == OrderType.MARKET:
                await self._try_fill_order_locked(order, float(resolve_px))
            else:
                # A resting limit may already be touchable at the last price.
                if resolve_px is not None:
                    await self._try_limit_touch_locked(order, float(resolve_px))
                self._fire_order_update_sync(order)
            return order

    async def _risk_gate(
        self,
        account_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
        mark_price: float | None,
    ) -> None:
        """Optional pre-trade check; inert without engine+flag."""
        if self._risk_engine is None or not _paper_risk_enforce_enabled():
            return
        try:
            from selene_space_types.agent import TradeIntent

            intent = TradeIntent(
                symbol=symbol,
                side=side,
                quantity=float(quantity),
                order_type=OrderType.MARKET,
                reasoning="paper execution risk gate",
                confidence=1.0,
                timestamp=_ms(),
                risk_params={"account_id": account_id},
            )
            decision = self._risk_engine.pre_check(intent, {"mark_price": mark_price})
            if not decision.allowed:
                raise OrderRejected(
                    f"paper order rejected by risk engine: {getattr(decision, 'reason', '')}"
                )
        except OrderRejected:
            raise
        except Exception:
            # A broken risk adapter must never silently block paper trading.
            return

    def _margin_ok(self, account_id: str, quantity: float, leverage: int, price: float) -> bool:
        account = self._account_mgr.get_account(account_id)
        if account is None:
            return False
        required = Decimal(str(quantity)) / Decimal(str(max(leverage, 1)))
        return account.current_balance >= required

    # ------------------------------------------------------------------
    # Fill pipelines (H1/H3/H8 core)
    # ------------------------------------------------------------------

    async def _try_fill_order(self, order: Order, trigger_price: float) -> Fill | None:
        """Unlocked entry point — grabs the owning account's lock itself."""
        account_id = self._get_order_account(order.id)
        lock = self._lock_for(account_id)
        async with lock:
            return await self._try_fill_order_locked(order, trigger_price)

    async def _try_fill_order_locked(self, order: Order, trigger_price: float) -> Fill | None:
        """Run one execution under the account lock, rolling back on failure.

        Snapshot everything mutable up front; any exception between the first
        mutation and completion restores the snapshot so callers never observe
        a half-applied fill.
        """
        maker_rate, taker_rate = self._get_fee_rates(self._owner_of(order.id))
        account_before = self._snapshot_account(self._owner_of(order.id))
        order_qty_before = order.filled_quantity
        order_avg_before = order.avg_fill_price
        order_status_before = order.status
        positions_len_before = len(self._position_mgr.get_all_data())

        try:
            fill = try_fill(
                order,
                trigger_price,
                maker_fee_rate=maker_rate,
                taker_fee_rate=taker_rate,
                latency_range_ms=None,
                slippage_model=self._slippage,
            )
            if fill is None:
                return None

            self._order_mgr.sync_fill(order.id)

            account_id = self._owner_of(order.id)
            account = self._account_mgr.get_account(account_id)
            assert account is not None

            fee_dec = Decimal(str(fill.fee))
            account.total_fee += fee_dec
            # Fees leave the wallet immediately, same as margin.
            account.current_balance -= fee_dec

            if not order.reduce_only:
                required = Decimal(str(fill.quantity)) / Decimal(max(order.leverage, 1))
                if account.current_balance < required:
                    raise ValueError("Insufficient margin")
                account.current_balance -= required

            position, realized = self._position_mgr.apply_fill_with_symbol(
                account_id,
                order.symbol,
                fill,
                order.side,
                leverage=order.leverage,
                margin_mode=order.margin_mode,
                pos_side=order.pos_side,
                available_balance=account.current_balance,
            )
            pnl_dec = Decimal(str(realized))
            if realized:
                account.total_realized_pnl += pnl_dec
                account.current_balance += pnl_dec

            self._fills.append(fill)

            # Mark-to-market right away so equity queries stay fresh.
            self._position_mgr.update_unrealized_pnl(
                account_id, order.symbol, trigger_price
            )

            await self._fire_order_update(order)
            await self._fire_fill(fill)
            await self._fire_position_update(position)
            balances = await self.get_balances(account_id)
            await self._fire_balance_update(balances)
            return fill
        except BaseException:
            self._rollback(
                order, order_qty_before, order_avg_before, order_status_before,
                account_before, positions_len_before,
            )
            raise

    def _rollback(
        self,
        order: Order,
        qty_before: Any,
        avg_before: Any,
        status_before: Any,
        account_before: dict[str, Any],
        positions_len_before: int,
    ) -> None:
        order.filled_quantity = qty_before
        order.avg_fill_price = avg_before
        order.status = status_before
        acct_id = self._owner_of(order.id)
        account = self._account_mgr.get_account(acct_id)
        if account is not None and account_before:
            account.current_balance = account_before["balance"]
            account.total_fee = account_before["fee"]
            account.total_realized_pnl = account_before["realized"]
        if len(self._position_mgr.get_all_data()) != positions_len_before:
            book = self._position_mgr.get_all_data().get(acct_id, {})
            book.pop(order.symbol, None)

    def _snapshot_account(self, account_id: str) -> dict[str, Any]:
        account = self._account_mgr.get_account(account_id)
        if account is None:
            return {}
        return {
            "balance": account.current_balance,
            "fee": account.total_fee,
            "realized": account.total_realized_pnl,
        }

    async def _try_limit_touch_locked(self, order: Order, current_price: float) -> None:
        """Fill a resting limit immediately when its price band already covers spot."""
        limit = order.price
        if limit is None:
            return
        px = float(limit)
        hit = current_price <= px if order.side == OrderSide.BUY else current_price >= px
        if hit:
            await self._try_fill_order_locked(order, min(current_price, px) if order.side == OrderSide.BUY else max(current_price, px))

    def _schedule_fill_task(self, account_id: str, order: Order, trigger_price: float) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(self._fill_job(account_id, order, trigger_price))
        task._paper_account = account_id  # type: ignore[attr-defined]
        self._fill_tasks.add(task)
        task.add_done_callback(self._fill_tasks.discard)
        return task

    async def _fill_job(self, account_id: str, order: Order, trigger_price: float) -> None:
        lock = self._lock_for(account_id)
        async with lock:
            await self._try_fill_order_locked(order, trigger_price)

    # ------------------------------------------------------------------
    # Price ticks
    # ------------------------------------------------------------------

    async def on_price_tick(self, symbol: str, price: float) -> None:
        """Feed a mark price; triggers resting limit fills and stop orders.

        Limit orders touch when the market crosses their limit; stop orders
        (STOP_MARKET / STOP_LIMIT) trigger only once price crosses their stop
        level in the adverse direction:

          - SELL stop (an exit for a long, or a short entry on breakdown)
            triggers when price falls to or below the stop;
          - BUY stop (an exit for a short, or a breakout entry) triggers when
            price rises to or above the stop.

        Same direction rule as the live path's StopManager, so a strategy that
        behaves one way in backtest/paper does not invert when it goes live.
        """
        self._last_price[symbol] = price
        candidates = [
            o for o in self._order_mgr.get_open_orders() if self._symbols_match(o.symbol, symbol)
        ]
        touched: list[tuple[str, Order]] = []
        for order in candidates:
            if order.stop_price is not None and order.type in (
                OrderType.STOP_MARKET,
                OrderType.STOP_LIMIT,
            ):
                stop = float(order.stop_price)
                triggered = (order.side == OrderSide.SELL and price <= stop) or (
                    order.side == OrderSide.BUY and price >= stop
                )
                if triggered:
                    touched.append((self._owner_of(order.id), order))
                continue

            limit = order.price
            if limit is None:
                continue
            px = float(limit)
            if (order.side == OrderSide.BUY and price <= px) or (
                order.side == OrderSide.SELL and price >= px
            ):
                touched.append((self._owner_of(order.id), order))
        for account_id, order in touched:
            await self._fill_job(account_id, order, price)

    # ------------------------------------------------------------------
    # Cancel / delete / reset
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str) -> Order:
        account_id = self._get_order_account(order_id)
        lock = self._lock_for(account_id)
        async with lock:
            cancelled = self._order_mgr.cancel_order(order_id)
            await self._fire_order_update(cancelled)
            return cancelled

    def delete_account(self, account_id: str) -> bool:
        return self._delete_account_sync(account_id)

    def _delete_account_sync(self, account_id: str) -> bool:
        existed = self._account_mgr.delete_account(account_id)
        self._position_mgr.clear_positions(account_id)
        self._order_mgr.clear_orders(account_id)
        self._fills = [
            f for f in self._fills if not self._fill_belongs_to(f, account_id)
        ]
        return existed

    async def delete_account_async(self, account_id: str, timeout: float = 5.0) -> bool:
        """H14: cancel this account's in-flight fills, take the lock, then delete."""
        for task in list(self._fill_tasks):
            owner = getattr(task, "_paper_account", account_id)
            if owner == account_id and not task.done():
                task.cancel()
        deadline = time.monotonic() + timeout
        for task in list(self._fill_tasks):
            if getattr(task, "_paper_account", account_id) == account_id:
                remaining = max(deadline - time.monotonic(), 0.0)
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
        lock = self._lock_for(account_id)
        async with lock:
            return self._delete_account_sync(account_id)

    def reset_account(self, account_id: str) -> Any:
        self._position_mgr.clear_positions(account_id)
        self._order_mgr.clear_orders(account_id)
        return self._account_mgr.reset_account(account_id)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_order_update(self, callback: Callable[[Order], Any]) -> None:
        self._cbs_order.append(callback)

    def on_fill(self, callback: Callable[[Fill], Any]) -> None:
        self._cbs_fill.append(callback)

    def on_position_update(self, callback: Callable[[Any], Any]) -> None:
        self._cbs_position.append(callback)

    def on_balance_update(self, callback: Callable[[list[Any]], Any]) -> None:
        self._cbs_balance.append(callback)

    def on_position_update_scoped(
        self, callback: Callable[[str, Any], Any]
    ) -> None:
        self._cbs_position.append(lambda pos, _cb=callback: _cb(pos.symbol, pos))

    def on_balance_update_scoped(
        self, callback: Callable[[str, list[Any]], Any]
    ) -> None:
        self._cbs_balance.append(
            lambda bal, _cb=callback: _cb("", bal)
        )

    # ------------------------------------------------------------------
    # Fire helpers (sync inside locked paths; async variants for external use)
    # ------------------------------------------------------------------

    def _fire_order_update_sync(self, order: Order) -> None:
        for cb in list(self._cbs_order):
            cb(order)

    def _fire_fill_sync(self, fill: Fill) -> None:
        for cb in list(self._cbs_fill):
            cb(fill)

    def _fire_position_sync(self, position: Any) -> None:
        for cb in list(self._cbs_position):
            cb(position)

    def _fire_balance_sync(self, balances: list[Any]) -> None:
        for cb in list(self._cbs_balance):
            cb(balances)

    async def _fire_order_update(self, order: Order) -> None:
        self._fire_order_update_sync(order)

    async def _fire_fill(self, fill: Fill) -> None:
        self._fire_fill_sync(fill)

    async def _fire_position_update(self, position: Any) -> None:
        self._fire_position_sync(position)

    async def _fire_balance_update(self, balances: list[Any]) -> None:
        self._fire_balance_sync(balances)

    @staticmethod
    async def _invoke(callbacks: list[Callable[..., Any]], *args: Any) -> None:
        for cb in list(callbacks):
            cb(*args)

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _symbols_match(a: str, b: str) -> bool:
        norm = lambda s: s.upper().replace("-", "").replace("/", "").replace("_", "")
        return norm(a) == norm(b)

    def _lock_for(self, account_id: str) -> asyncio.Lock:
        lock = self._account_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._account_locks[account_id] = lock
        return lock

    def _get_order_account(self, order_id: str) -> str:
        return self._owner_of(order_id)

    def _owner_of(self, order_id: str) -> str:
        owner = getattr(self._order_mgr, "_owner", {}).get(order_id)
        return owner or self._default_account_id

    def _fill_belongs_to(self, fill: Fill, account_id: str) -> bool:
        return self._owner_of(fill.order_id) == account_id
