"""Grid execution engine — fill-driven grid runtime for perpetual DEXes.

Inspired by a Node.js grid trading bot's runtime pattern (clean-room reimplementation, no source code copied). The grid engine is NOT a
kline-driven strategy — it is a self-contained runtime driven by fill and price
events from a PerpetualDEXMixin adapter.

Core loop:
  1. Seed initial ladder of limit orders via ``seed_orders()``
  2. On every fill → place the opposite order one rung away via ``replacement_for()``
  3. On every price tick → check out-of-range, manage recovery
  4. Periodically reconcile against the real exchange order book
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from selene_space_exchange.dex.perpetual_mixin import PerpetualDEXMixin
from selene_space_exchange.dex.types import (
    DEXFill,
    DEXPriceUpdate,
    GridMode,
)
from selene_space_paper.grid_math import (
    FilledOrder,
    GridSpec,
    build_grid,
    replacement_for,
    seed_orders,
)
from selene_space_paper.grid_types import (
    GridConfig,
    GridHealth,
    GridState,
    GridStats,
    RangeAdjustment,
)
from selene_space_types.trading import OrderSide

logger = logging.getLogger(__name__)

RECONCILE_SEC: float = 30.0
PRUNE_GRACE_MS: float = 20_000.0
_MAX_FILLS_HISTORY: int = 50
_MAX_ALERTS_HISTORY: int = 30
_CANCEL_BURST_WINDOW_MS: float = 60_000.0
_CANCEL_BURST_THRESHOLD: int = 5
_REFILL_PAUSE_MS: float = 60_000.0
_RETRY_MAX_TRIES: int = 5
_CLOSE_CONFIRM_RETRIES: int = 3
_CLOSE_CONFIRM_WAIT_SEC: float = 8.0
_RECOVERY_NO_POS_STREAK: int = 5
_HARD_MAX_RECOVERY_RUNGS: int = 80


def _round2(x: float) -> float:
    return round(x, 2)


def _round6(x: float) -> float:
    return round(x, 6)


# ---------------------------------------------------------------------------
# Tracked order (internal)
# ---------------------------------------------------------------------------


@dataclass
class _ActiveOrder:
    """Internal tracking for a resting order on the exchange."""

    level_index: int
    side: OrderSide
    price: float
    size_base: float
    opening: bool = True
    recovery: bool = False
    placed_at: float = 0.0
    gone_recon: int = 0

    def __post_init__(self) -> None:
        if self.placed_at == 0.0:
            self.placed_at = time.time() * 1000


# ---------------------------------------------------------------------------
# Retry queue entry
# ---------------------------------------------------------------------------


@dataclass
class _RetryEntry:
    """A closing-leg placement awaiting retry."""

    level_index: int
    side: OrderSide
    price: float
    size_base: float
    reduce_only: bool
    recovery: bool
    opening: bool
    tries: int = 0
    next_at: float = 0.0


# ---------------------------------------------------------------------------
# GridExecutionEngine
# ---------------------------------------------------------------------------


class GridExecutionEngine:
    """Fill-driven grid execution runtime for perpetual DEXes.

    Takes a ``PerpetualDEXMixin`` adapter and drives a grid strategy:
    places orders, handles fills, manages out-of-range scenarios, and
    provides crash-safe persistence via ``snapshot()`` / ``resume()``.
    """

    def __init__(self, exchange: PerpetualDEXMixin) -> None:
        self._ex = exchange

        # Grid state
        self.running: bool = False
        self.config: GridConfig | None = None
        self.grid: GridSpec | None = None
        self.active: dict[str, _ActiveOrder] = {}
        self.fills: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.stats: GridStats = GridStats()
        self.start_balance: float | None = None
        self.last_price: float | None = None
        self.out_of_range: bool = False
        self.risk: dict[str, Any] | None = None
        self.recovery: bool = False

        # Internal guards / counters
        self._stopping: bool = False
        self._starting: bool = False
        self._coid_seq: int = 0
        self._place_fails: int = 0
        self._last_fail_at: float = 0.0
        self._exchange_open_orders: int | None = None
        self._pending_levels: set[int] = set()
        self._recovery_occupied: set[int] = set()
        self._cancel_times: list[float] = []
        self._refill_paused_until: float = 0.0
        self._last_err_alert_at: float = 0.0
        self._last_err_log_at: float = 0.0
        self._retry_queue: list[_RetryEntry] = []
        self._no_pos_streak: int = 0
        self._pnl_base: float | None = None
        self._last_vanish_alert_at: float = 0.0

        # Reconcile timer
        self._recon_task: asyncio.Task | None = None

        # Persistence callback
        self._on_change: Callable[[dict[str, Any]], None] | None = None

        # Event callbacks (external subscribers)
        self._order_update_cbs: list[Callable[[Any], None]] = []
        self._fill_cbs: list[Callable[[Any], None]] = []
        self._position_update_cbs: list[Callable[[Any], None]] = []
        self._balance_update_cbs: list[Callable[[Any], None]] = []

        # Register on the exchange adapter
        self._ex.on_fill(self._handle_fill)
        self._ex.on_price(self._handle_price)
        self._ex.on_error(self._handle_exchange_error)

    # ------------------------------------------------------------------
    # Public API: grid lifecycle
    # ------------------------------------------------------------------

    async def start_grid(self, cfg: dict[str, Any]) -> GridState:
        """Start a new grid. ``cfg`` keys: marketId, mode, lower, upper, gridCount, sizeBase, leverage, outOfRangeAction."""
        if self.running or self._starting:
            raise ValueError("Grid already running or starting.")
        self._starting = True
        try:
            return await self._start_grid(cfg)
        finally:
            self._starting = False

    async def _start_grid(self, cfg: dict[str, Any]) -> GridState:
        markets = await self._ex.get_markets()
        market = next((m for m in markets if m.market_id == int(cfg["marketId"])), None)
        if market is None:
            raise ValueError(f"Market not found: marketId={cfg['marketId']}")

        leverage = min(int(cfg.get("leverage", 3)), market.max_leverage or 50)
        size_base = max(float(cfg.get("sizeBase", 0)), market.min_order_size or 0)
        mode = cfg.get("mode", "neutral")

        self.config = GridConfig(
            market_id=market.market_id,
            display_name=market.display_name or market.name,
            mode=mode,
            lower=float(cfg["lower"]),
            upper=float(cfg["upper"]),
            grid_count=int(cfg["gridCount"]),
            size_base=size_base,
            leverage=leverage,
            out_of_range_action=cfg.get("outOfRangeAction", "close"),
        )

        self.grid = build_grid(
            lower=self.config.lower, upper=self.config.upper, grid_count=self.config.grid_count
        )
        self._recompute_risk()
        self._refill_paused_until = 0.0
        self._cancel_times = []
        self._retry_queue = []
        self._no_pos_streak = 0
        self.recovery = False

        # Record starting equity
        if self.start_balance is None:
            eq = self._ex.equity
            bal = self._ex.balance
            self.start_balance = eq if isinstance(eq, (int, float)) else (bal if isinstance(bal, (int, float)) else None)

        # Margin pre-check
        if self.risk and self.start_balance is not None:
            required = self.risk["required_margin"]
            available = self._ex.equity
            if not isinstance(available, (int, float)):
                available = self._ex.balance
            if isinstance(available, (int, float)):
                if required > available:
                    raise ValueError(
                        f"Insufficient margin: need ~{_round2(required)} USDC "
                        f"(notional {self.risk['notional']}, {leverage}x), "
                        f"available {_round2(available)} USDC."
                    )
                if required > available * 0.8:
                    self._alert(
                        f"Margin usage high: ~{_round2(required)} / {_round2(available)} USDC (>80%), "
                        "liquidation risk on volatility."
                    )

        # Fee vs spacing sanity check
        if self.risk and self.risk["spacing_pct"] <= 0.1:  # rough check
            self._alert(
                f"Grid spacing {self.risk['spacing_pct']}% may not cover round-trip fees."
            )

        # Set leverage
        lev_ok = await self._ex.set_leverage(market.market_id, leverage)
        if not lev_ok:
            self._alert(f"Leverage {leverage}x may not have taken effect — verify on exchange.")

        # Cancel any existing orders
        await self._ex.cancel_all(market.market_id)

        # Get current price
        price = await self._ex.get_price(market.market_id)
        if not price or price <= 0:
            raise ValueError("Cannot get a valid current price — aborting grid start.")
        if price < self.config.lower * 0.5 or price > self.config.upper * 2:
            raise ValueError(
                f"Price {price} too far from grid range "
                f"[{self.config.lower}, {self.config.upper}]."
            )
        self.last_price = price
        self.out_of_range = price < self.config.lower or price > self.config.upper

        # Seed the ladder
        seeds = seed_orders(
            levels=self.grid.levels,
            price=price,
            mode=GridMode(self.config.mode),
            spacing=self.grid.spacing,
        )
        for s in seeds:
            await self._place(
                level_index=s.level_index,
                side=s.side,
                price=s.price,
                size_base=size_base,
                reduce_only=s.reduce_only,
                opening=True,
            )

        if self.start_balance is None and isinstance(self._ex.balance, (int, float)):
            self.start_balance = self._ex.balance

        self.running = True
        self._start_reconcile_timer()
        self._alert(
            f"Grid started: {self.config.display_name} {self.config.mode}, "
            f"{self.grid.count} cells, spacing {self.grid.spacing} "
            f"({self.risk['spacing_pct']}%), {leverage}x, "
            f"{len(self.active)} orders placed."
        )
        self._notify_change()
        return self.get_state()

    async def stop_grid(self, close_position: bool = True) -> GridState:
        """Stop the grid: cancel orders, optionally close position."""
        self._stop_reconcile_timer()
        if not self.running:
            if self.config:
                await self._ex.cancel_all(self.config.market_id)
                if close_position:
                    await self._close_with_confirm(self.config.market_id)
                self._alert("Attempted cancel-all and close position.")
            self.active.clear()
            self._retry_queue = []
            self._notify_change()
            return self.get_state()

        self._ex.on_fill(self._handle_fill)  # re-register to remove later
        await self._ex.cancel_all(self.config.market_id)
        self.active.clear()
        close_requested = False
        if close_position:
            await self._close_with_confirm(self.config.market_id)
            close_requested = True
        self.running = False
        self.recovery = False
        self._retry_queue = []
        self._alert(
            "Grid stopped: orders cancelled, "
            + ("close position sent." if close_requested else "position left open.")
        )
        self._notify_change()
        return self.get_state()

    async def cancel_all_orders(self) -> GridState:
        """Cancel all orders without touching the position. Stops the grid."""
        if not self.config:
            raise ValueError("No market configured — nothing to cancel.")
        self._stop_reconcile_timer()
        await self._ex.cancel_all(self.config.market_id)
        self.active.clear()
        self.running = False
        self._refill_paused_until = 0.0
        self._cancel_times = []
        self._retry_queue = []
        self._alert("All orders cancelled (position untouched). Grid stopped.")
        self._notify_change()
        return self.get_state()

    async def adjust_range(self, adjustment: RangeAdjustment) -> GridState:
        """Adjust the grid's price range without stopping. Position is preserved."""
        if not self.running or not self.config:
            raise ValueError("Grid not running — cannot adjust range.")
        lo, hi = adjustment.lower, adjustment.upper
        if not hi > lo:
            raise ValueError("Upper must be greater than lower.")
        price = self.last_price
        if price and (price < lo * 0.5 or price > hi * 2):
            raise ValueError(f"New range [{lo}, {hi}] too far from current price {price}.")

        new_grid = build_grid(lower=lo, upper=hi, grid_count=self.config.grid_count)
        mid = (lo + hi) / 2
        notional = new_grid.count * self.config.size_base * mid
        required_margin = notional / self.config.leverage
        available = self._ex.equity
        if not isinstance(available, (int, float)):
            available = self._ex.balance
        if isinstance(available, (int, float)) and required_margin > available:
            raise ValueError(
                f"Insufficient margin for new range: need ~{_round2(required_margin)}, "
                f"available {_round2(available)}."
            )

        await self._ex.cancel_all(self.config.market_id)
        self.active.clear()
        self._refill_paused_until = 0.0
        self._cancel_times = []
        self.config = GridConfig(
            market_id=self.config.market_id,
            display_name=self.config.display_name,
            mode=self.config.mode,
            lower=lo,
            upper=hi,
            grid_count=self.config.grid_count,
            size_base=self.config.size_base,
            leverage=self.config.leverage,
            out_of_range_action=self.config.out_of_range_action,
        )
        self.grid = new_grid
        self._recompute_risk()
        self.out_of_range = (price < lo or price > hi) if price else False

        if not self.out_of_range and price and price > 0:
            seeds = seed_orders(
                levels=new_grid.levels,
                price=price,
                mode=GridMode(self.config.mode),
                spacing=new_grid.spacing,
            )
            for s in seeds:
                await self._place(
                    level_index=s.level_index,
                    side=s.side,
                    price=s.price,
                    size_base=self.config.size_base,
                    reduce_only=s.reduce_only,
                    opening=True,
                )

        self._alert(
            f"Range adjusted to [{lo}, {hi}], {new_grid.count} cells, "
            f"spacing {new_grid.spacing}, {len(self.active)} orders re-placed."
        )
        self._notify_change()
        return self.get_state()

    async def start_recovery(self, cfg: dict[str, Any]) -> GridState:
        """Start a standalone reduce-only recovery ladder for an existing position."""
        if self.running or self._starting:
            raise ValueError("Already running — stop first.")
        self._starting = True
        try:
            return await self._start_recovery(cfg)
        finally:
            self._starting = False

    async def _start_recovery(self, cfg: dict[str, Any]) -> GridState:
        markets = await self._ex.get_markets()
        market = next((m for m in markets if m.market_id == int(cfg["marketId"])), None)
        if market is None:
            raise ValueError(f"Market not found: marketId={cfg['marketId']}")

        pos = self._ex.get_position(market.market_id)
        if pos is None or pos.size_base == 0:
            raise ValueError("No position to recover.")

        price = await self._ex.get_price(market.market_id)
        if not price or price <= 0:
            raise ValueError("Cannot get a valid price.")

        spacing = float(cfg.get("spacing", 0)) or (self.grid.spacing if self.grid else 0)
        if spacing <= 0:
            spacing = max(market.step_price or 0.1, price * 0.0015)
        size_base = float(cfg.get("sizeBase", 0)) or self.config.size_base if self.config else 0
        if size_base <= 0:
            size_base = abs(pos.size_base) / 20
        size_base = max(size_base, market.min_order_size or 0)

        self.config = GridConfig(
            market_id=market.market_id,
            display_name=market.display_name or market.name,
            mode="recovery",
            lower=0,
            upper=0,
            grid_count=0,
            size_base=size_base,
            leverage=pos.leverage or 0,
            out_of_range_action="recover",
        )
        self.grid = None
        self.risk = None
        self.recovery = True
        self.out_of_range = False
        self.last_price = price
        self._no_pos_streak = 0
        self._retry_queue = []
        self.active.clear()
        self._recovery_occupied = set()

        if self.start_balance is None:
            eq = self._ex.equity
            bal = self._ex.balance
            self.start_balance = eq if isinstance(eq, (int, float)) else (bal if isinstance(bal, (int, float)) else None)

        self.running = True
        self._recovery_occupied = set()
        await self.reconcile_open_orders()
        self._manage_recovery_standalone()
        self._start_reconcile_timer()
        direction = "long" if pos.size_base > 0 else "short"
        self._alert(
            f"Recovery ladder started for {market.display_name} {direction} "
            f"{_round6(abs(pos.size_base))}: reduce-only, no auto-stop."
        )
        self._notify_change()
        return self.get_state()

    async def close_position_now(self, market_id: int | None = None) -> GridState:
        """Cancel all orders and market-close the position."""
        m_id = market_id or (self.config.market_id if self.config else None)
        if m_id is None:
            raise ValueError("No market specified.")
        self._stop_reconcile_timer()
        await self._ex.cancel_all(m_id)
        self.active.clear()
        self._retry_queue = []
        closed = await self._close_with_confirm(m_id)
        self.running = False
        self.recovery = False
        self._alert("Position close requested." if closed else "Orders cancelled (auto-close unavailable).")
        self._notify_change()
        return self.get_state()

    def reset_stats(self) -> GridState:
        """Zero cumulative stats and re-baseline PnL."""
        self.stats = GridStats()
        self.fills = []
        self._place_fails = 0
        self._last_fail_at = 0.0
        self._refill_paused_until = 0.0
        self._cancel_times = []
        eq = self._ex.equity
        bal = self._ex.balance
        self.start_balance = eq if isinstance(eq, (int, float)) else (bal if isinstance(bal, (int, float)) else self.start_balance)
        rpnl = self._ex.realized_pnl
        self._pnl_base = rpnl if isinstance(rpnl, (int, float)) else None
        self._alert("Stats reset: PnL, volume, completed rungs zeroed.")
        self._notify_change()
        return self.get_state()

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_state(self) -> GridState:
        """Return full grid state as a GridState Pydantic model."""
        pos = self._ex.get_position(self.config.market_id) if self.config else None
        unrealized = _round2(pos.unrealized_pnl) if pos else 0.0
        balance = self._ex.balance if isinstance(self._ex.balance, (int, float)) else None
        equity_raw = self._ex.equity if isinstance(self._ex.equity, (int, float)) else None
        if equity_raw is None and balance is not None:
            equity_raw = balance + unrealized
        equity = _round2(equity_raw) if equity_raw is not None else None

        rpnl = self._ex.realized_pnl
        if isinstance(rpnl, (int, float)):
            realized = _round2(rpnl - (self._pnl_base or 0))
        elif equity_raw is not None and self.start_balance is not None:
            realized = _round2((equity_raw - self.start_balance) - unrealized)
        else:
            realized = _round2(self.stats.grid_profit)

        total_pnl = _round2(realized + unrealized)
        return_pct = None
        if self.start_balance and self.start_balance > 0:
            return_pct = _round2((total_pnl / self.start_balance) * 100)
        elif equity and equity > 0:
            return_pct = _round2((total_pnl / equity) * 100)

        return GridState(
            running=self.running,
            recovery=self.recovery,
            config=self.config,
            last_price=_round2(self.last_price) if self.last_price else None,
            out_of_range=self.out_of_range,
            balance=_round2(balance) if balance is not None else None,
            equity=equity,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_pnl=total_pnl,
            return_pct=return_pct or 0.0,
            volume=self.stats.volume,
            open_orders=len(self.active),
            exchange_open_orders=self._exchange_open_orders,
            completed_rungs=self.stats.completed_rungs,
            health=self._health(),
            stats=self.stats,
        )

    def snapshot(self) -> dict[str, Any]:
        """Durable snapshot for crash recovery / resume."""
        active_entries = [
            [oid, {
                "level_index": o.level_index,
                "side": o.side.value,
                "price": o.price,
                "size_base": o.size_base,
                "opening": o.opening,
                "recovery": o.recovery,
                "placed_at": o.placed_at,
            }]
            for oid, o in self.active.items()
        ]
        return {
            "running": self.running,
            "config": self.config.model_dump() if self.config else None,
            "stats": self.stats.model_dump(),
            "recovery": self.recovery,
            "pnl_base": self._pnl_base,
            "start_balance": self.start_balance,
            "out_of_range": self.out_of_range,
            "last_price": self.last_price,
            "active": active_entries,
        }

    async def resume(self, snap: dict[str, Any]) -> GridState:
        """Resume a grid from a snapshot after process restart."""
        if not snap or not snap.get("config"):
            raise ValueError("No valid snapshot to resume.")
        if self.running:
            raise ValueError("Already running — cannot resume.")

        cfg_data = snap["config"]
        self.config = GridConfig(**cfg_data)
        self.stats = GridStats(**(snap.get("stats", {})))
        self.start_balance = snap.get("start_balance")
        self._pnl_base = snap.get("pnl_base")
        self.out_of_range = bool(snap.get("out_of_range", False))
        self.last_price = snap.get("last_price")

        if snap.get("recovery") or cfg_data.get("mode") == "recovery":
            return await self._resume_recovery(snap)

        self.grid = build_grid(
            lower=self.config.lower, upper=self.config.upper, grid_count=self.config.grid_count
        )
        self._recompute_risk()

        # Rebuild active map and adopt orders on the exchange
        self.active.clear()
        for entry in snap.get("active", []):
            oid = str(entry[0])
            info = entry[1]
            side = OrderSide(info["side"]) if isinstance(info["side"], str) else info["side"]
            ao = _ActiveOrder(
                level_index=info["level_index"],
                side=side,
                price=info["price"],
                size_base=info.get("size_base", self.config.size_base),
                opening=info.get("opening", True),
                recovery=info.get("recovery", False),
                placed_at=info.get("placed_at", time.time() * 1000),
            )
            self.active[oid] = ao
            try:
                self._ex.adopt_order(
                    order_id=oid,
                    market_id=self.config.market_id,
                    level_index=ao.level_index,
                    side=ao.side,
                    price=ao.price,
                    size_base=ao.size_base,
                )
            except Exception:
                pass

        self.running = True
        self._start_reconcile_timer()
        self._alert(
            f"Resumed grid: {self.config.display_name}, "
            f"{len(self.active)} orders adopted, reconciling..."
        )
        await self.reconcile_open_orders()
        self._notify_change()
        return self.get_state()

    async def _resume_recovery(self, snap: dict[str, Any]) -> GridState:
        """Resume a standalone recovery ladder after restart."""
        self.grid = None
        self.risk = None
        self.recovery = True
        self.out_of_range = False
        self._no_pos_streak = 0
        self._retry_queue = []
        self.active.clear()

        for entry in snap.get("active", []):
            oid = str(entry[0])
            info = entry[1]
            side = OrderSide(info["side"]) if isinstance(info["side"], str) else info["side"]
            ao = _ActiveOrder(
                level_index=info["level_index"],
                side=side,
                price=info["price"],
                size_base=info.get("size_base", self.config.size_base),
                opening=info.get("opening", True),
                recovery=info.get("recovery", True),
                placed_at=info.get("placed_at", time.time() * 1000),
            )
            self.active[oid] = ao
            try:
                self._ex.adopt_order(
                    order_id=oid,
                    market_id=self.config.market_id,
                    level_index=ao.level_index,
                    side=ao.side,
                    price=ao.price,
                    size_base=ao.size_base,
                )
            except Exception:
                pass

        px = await self._ex.get_price(self.config.market_id)
        if px and px > 0:
            self.last_price = px

        self._recovery_occupied = set()
        self.running = True
        await self.reconcile_open_orders()
        self._start_reconcile_timer()
        self._alert(f"Resumed recovery ladder: {len(self.active)} orders adopted.")
        self._notify_change()
        return self.get_state()

    # ------------------------------------------------------------------
    # Internal: fill handling
    # ------------------------------------------------------------------

    def _handle_fill(self, fill: DEXFill) -> None:
        """Handle a fill event from the DEX adapter."""
        if not self.running or not self.config:
            return
        if fill.market_id != self.config.market_id:
            return

        oid = fill.order_id
        act = self.active.pop(oid, None)
        level_index = act.level_index if act else fill.level_index
        fill_price = fill.price if fill.price and fill.price == fill.price else (act.price if act else 0)
        fill_size = fill.size_base if fill.size_base else self.config.size_base

        if fill.side == OrderSide.BUY:
            self.stats.buys += 1
        else:
            self.stats.sells += 1

        self.stats.volume = _round2(self.stats.volume + fill_price * fill_size)
        self.fills.insert(0, {
            "t": time.time() * 1000,
            "side": fill.side.value,
            "price": fill_price,
            "size": fill_size,
            "level": level_index,
        })
        if len(self.fills) > _MAX_FILLS_HISTORY:
            self.fills.pop()

        is_recovery = act.recovery if act else False
        closing = is_recovery or (not act.opening if act else (fill.side == OrderSide.SELL if self.config.mode != "short" else fill.side == OrderSide.BUY))

        if closing:
            self.stats.completed_rungs += 1
            sp = self.grid.spacing if self.grid else 0
            self.stats.grid_profit = _round2(self.stats.grid_profit + sp * fill_size)

        # Place replacement (not for recovery fills)
        if not is_recovery and self.grid:
            filled = FilledOrder(
                level_index=level_index,
                price=fill_price,
                side=fill.side,
            )
            repl = replacement_for(filled, self.grid.levels, GridMode(self.config.mode))
            if repl and not self.out_of_range and self.running:
                asyncio.ensure_future(self._place(
                    level_index=repl.level_index,
                    side=repl.side,
                    price=repl.price,
                    size_base=fill_size if fill_size > 0 else self.config.size_base,
                    reduce_only=repl.reduce_only,
                    opening=closing,
                ))

        self._notify_change()

    # ------------------------------------------------------------------
    # Internal: price handling
    # ------------------------------------------------------------------

    def _handle_price(self, update: DEXPriceUpdate) -> None:
        """Handle a price update from the DEX adapter."""
        if not self.running or not self.config:
            return
        if update.market_id != self.config.market_id:
            return

        self.last_price = update.price
        self._drain_retry_queue()

        if self.recovery:
            self._manage_recovery_standalone()
            return

        out = update.price < self.config.lower or update.price > self.config.upper
        action = self.config.out_of_range_action

        if out and not self.out_of_range:
            self.out_of_range = True
            where = "below lower" if update.price < self.config.lower else "above upper"
            if action == "recover":
                self._alert(f"Price {where} ({_round2(update.price)}), enabling recovery ladder.")
                self._place_recovery_ladder()
            else:
                self._alert(f"Price {where} ({_round2(update.price)}), triggering auto-stop.")
                if not self._stopping:
                    self._stopping = True
                    async def _do_stop() -> None:
                        try:
                            await self.stop_grid(close_position=True)
                        finally:
                            self._stopping = False
                    asyncio.ensure_future(_do_stop())
        elif out and self.out_of_range and action == "recover":
            self._place_recovery_ladder()
        elif not out and self.out_of_range:
            self.out_of_range = False
            asyncio.ensure_future(self._cancel_recovery_ladder())
            self._alert(f"Price back in range ({_round2(update.price)}), resuming normal grid.")

    # ------------------------------------------------------------------
    # Internal: exchange error handling
    # ------------------------------------------------------------------

    def _handle_exchange_error(self, exc: Exception) -> None:
        """Handle errors from the exchange adapter."""
        msg = str(exc)
        now = time.time() * 1000

        if now - self._last_err_log_at > 3000:
            self._last_err_log_at = now
            logger.error("[Exchange event] %s", msg)

        if now - self._last_err_alert_at > 5000:
            self._last_err_alert_at = now
            self._alert(f"Exchange event: {msg}")

        # Detect burst of cancellations (collateral exhaustion / manual intervention)
        if any(kw in msg.lower() for kw in ("cancel", "collateral", "reject")):
            self._cancel_times.append(now)
            self._cancel_times = [t for t in self._cancel_times if now - t < _CANCEL_BURST_WINDOW_MS]
            if len(self._cancel_times) >= _CANCEL_BURST_THRESHOLD and now >= self._refill_paused_until:
                self._refill_paused_until = now + _REFILL_PAUSE_MS
                self._alert(
                    "Detected burst of order cancellations — pausing auto-refill for 60s."
                )

    # ------------------------------------------------------------------
    # Internal: order placement
    # ------------------------------------------------------------------

    async def _place(
        self,
        *,
        level_index: int,
        side: OrderSide,
        price: float,
        size_base: float,
        reduce_only: bool = False,
        opening: bool = True,
        recovery: bool = False,
    ) -> None:
        """Place a single grid order with dedup and back-off guards."""
        # Back-off: don't place new OPENING orders while paused
        if opening and not recovery and self._refill_paused_until and time.time() * 1000 < self._refill_paused_until:
            return

        # Level dedup: at most one order per level
        if level_index in self._pending_levels:
            return
        for ao in self.active.values():
            if ao.level_index == level_index:
                return

        self._pending_levels.add(level_index)
        self._coid_seq = (self._coid_seq + 1) % 1_000_000
        coid = int(time.time() * 1000) % 10_000_000 * 100 + self._coid_seq

        try:
            result = await self._ex.place_limit_order(
                market_id=self.config.market_id,
                side=side,
                price=price,
                size_base=size_base,
                reduce_only=reduce_only,
                level_index=level_index,
                client_order_id=coid,
            )
            order_id = str(result.get("order_id", ""))
            if order_id:
                self.active[order_id] = _ActiveOrder(
                    level_index=level_index,
                    side=side,
                    price=price,
                    size_base=size_base,
                    opening=opening,
                    recovery=recovery,
                )
        except Exception as exc:
            self._place_fails += 1
            self._last_fail_at = time.time() * 1000
            self._alert(f"Order placement failed: {exc}")
            # Queue closing/recovery legs for retry
            if not opening or reduce_only or recovery:
                self._queue_retry(
                    level_index=level_index,
                    side=side,
                    price=price,
                    size_base=size_base,
                    reduce_only=reduce_only,
                    recovery=recovery,
                    opening=opening,
                )
        finally:
            self._pending_levels.discard(level_index)

    def _queue_retry(
        self,
        *,
        level_index: int,
        side: OrderSide,
        price: float,
        size_base: float,
        reduce_only: bool,
        recovery: bool,
        opening: bool,
        tries: int = 0,
    ) -> None:
        """Queue a closing-leg placement for retry."""
        if opening and not reduce_only and not recovery:
            return  # Only closing legs are retried
        tries += 1
        if tries > _RETRY_MAX_TRIES:
            self._alert(f"Gave up retrying closing order at level {level_index} @ {price}.")
            return
        self._retry_queue.append(_RetryEntry(
            level_index=level_index,
            side=side,
            price=price,
            size_base=size_base,
            reduce_only=reduce_only,
            recovery=recovery,
            opening=opening,
            tries=tries,
            next_at=time.time() * 1000 + 5000 * tries,
        ))

    def _drain_retry_queue(self) -> None:
        """Retry due closing-leg placements."""
        if not self.running or not self._retry_queue:
            return
        now = time.time() * 1000
        due: list[_RetryEntry] = []
        remaining: list[_RetryEntry] = []
        for entry in self._retry_queue:
            if entry.next_at <= now:
                due.append(entry)
            else:
                remaining.append(entry)
        self._retry_queue = remaining
        for entry in due:
            asyncio.ensure_future(self._place(
                level_index=entry.level_index,
                side=entry.side,
                price=entry.price,
                size_base=entry.size_base,
                reduce_only=entry.reduce_only,
                opening=entry.opening,
                recovery=entry.recovery,
            ))

    # ------------------------------------------------------------------
    # Internal: recovery ladder
    # ------------------------------------------------------------------

    def _place_recovery_ladder(self) -> None:
        """Place reduce-only recovery ladder when price exits the grid range."""
        if not self.running or not self.out_of_range or not self.grid:
            return
        if self.config.out_of_range_action != "recover":
            return

        price = self.last_price
        if not price or price <= 0:
            return
        pos = self._ex.get_position(self.config.market_id)
        if not pos or pos.size_base == 0:
            return

        sp = self.grid.spacing
        lvl0 = self.grid.levels[0]
        long = pos.size_base > 0
        existing = {ao.level_index for ao in self.active.values() if ao.recovery}
        max_rungs = self.grid.count
        placed = 0

        def room() -> bool:
            return len(existing) + placed < max_rungs

        if long and price < self.config.lower:
            lv = self.config.lower - sp
            while lv > price and room():
                idx = round((lv - lvl0) / sp)
                if idx not in existing:
                    asyncio.ensure_future(self._place(
                        level_index=idx, side=OrderSide.SELL, price=lv,
                        size_base=self.config.size_base, reduce_only=True,
                        opening=False, recovery=True,
                    ))
                    placed += 1
                lv -= sp
        elif not long and price > self.config.upper:
            lv = self.config.upper + sp
            while lv < price and room():
                idx = round((lv - lvl0) / sp)
                if idx not in existing:
                    asyncio.ensure_future(self._place(
                        level_index=idx, side=OrderSide.BUY, price=lv,
                        size_base=self.config.size_base, reduce_only=True,
                        opening=False, recovery=True,
                    ))
                    placed += 1
                lv += sp

        if placed:
            self._alert(f"Recovery ladder: {placed} reduce-only orders placed.")
            self._notify_change()

    async def _cancel_recovery_ladder(self) -> None:
        """Cancel all recovery-ladder orders (price returned in range)."""
        ids = [oid for oid, ao in self.active.items() if ao.recovery]
        if not ids:
            return
        for oid in ids:
            try:
                await self._ex.cancel_order_by_id(self.config.market_id, oid)
            except Exception:
                pass
            self.active.pop(oid, None)
        self._alert(f"Cancelled {len(ids)} recovery ladder orders.")
        self._notify_change()

    def _manage_recovery_standalone(self) -> None:
        """Maintain reduce-only exit ladder for standalone recovery mode."""
        if not self.running or not self.recovery or not self.config:
            return
        price = self.last_price
        if not price or price <= 0:
            return

        pos = self._ex.get_position(self.config.market_id)
        if not pos or pos.size_base == 0:
            self._no_pos_streak += 1
            if self._no_pos_streak >= _RECOVERY_NO_POS_STREAK:
                self._finish_recovery()
            return
        self._no_pos_streak = 0

        sp = float(self.config.step_size) if self.config.step_size else 0
        if sp <= 0 and self.grid:
            sp = self.grid.spacing
        if sp <= 0:
            return

        long = pos.size_base > 0
        existing = {ao.level_index for ao in self.active.values() if ao.recovery}
        for idx in self._recovery_occupied:
            existing.add(idx)

        per_rung = self.config.size_base or abs(pos.size_base) / 20
        need = min(_HARD_MAX_RECOVERY_RUNGS, max(1, int(abs(pos.size_base) / per_rung)))
        placed = 0

        if long:
            lv = round(price / sp) * sp
            if lv <= price:
                lv += sp
            for _ in range(_HARD_MAX_RECOVERY_RUNGS):
                if len(existing) + placed >= need:
                    break
                idx = round(lv / sp)
                if idx not in existing:
                    asyncio.ensure_future(self._place(
                        level_index=idx, side=OrderSide.SELL, price=lv,
                        size_base=self.config.size_base, reduce_only=True,
                        opening=False, recovery=True,
                    ))
                    placed += 1
                lv += sp
        else:
            lv = round(price / sp) * sp
            if lv >= price:
                lv -= sp
            for _ in range(_HARD_MAX_RECOVERY_RUNGS):
                if len(existing) + placed >= need:
                    break
                idx = round(lv / sp)
                if idx not in existing:
                    asyncio.ensure_future(self._place(
                        level_index=idx, side=OrderSide.BUY, price=lv,
                        size_base=self.config.size_base, reduce_only=True,
                        opening=False, recovery=True,
                    ))
                    placed += 1
                lv -= sp

        if placed:
            self._notify_change()

    def _finish_recovery(self) -> None:
        """Recovery complete — position fully reduced."""
        if not self.recovery:
            return
        self.recovery = False
        self._stop_reconcile_timer()
        self._recovery_occupied = set()
        self.running = False
        asyncio.ensure_future(self._ex.cancel_all(self.config.market_id))
        self.active.clear()
        self._alert("Recovery complete: position fully reduced.")
        self._notify_change()

    # ------------------------------------------------------------------
    # Internal: reconciliation
    # ------------------------------------------------------------------

    async def reconcile_open_orders(self) -> None:
        """Reconcile tracked orders against the real exchange order book."""
        if not self.running or not self.config:
            return

        # Keep price watch warm
        try:
            await self._ex.get_price(self.config.market_id)
        except Exception:
            pass

        self._drain_retry_queue()

        is_recovery = self.recovery
        sp = self.config.step_size if is_recovery and self.config.step_size else (self.grid.spacing if self.grid else 0)
        lvl0 = self.grid.levels[0] if self.grid else 0
        if sp <= 0 or (lvl0 is None and not is_recovery):
            return

        n_levels = len(self.grid.levels) if self.grid else float("inf")
        ladder_pad = 0
        if not is_recovery and self.config.out_of_range_action == "recover" and self.grid:
            ladder_pad = self.grid.count
        idx_lo = 0 - ladder_pad
        idx_hi = n_levels + ladder_pad if n_levels != float("inf") else float("inf")

        real: list[dict[str, Any]] | None = None
        try:
            real = await self._ex.fetch_open_orders(self.config.market_id)
        except Exception:
            return
        if not isinstance(real, list):
            return

        self._exchange_open_orders = len(real)
        real_ids = {str(o.get("order_id", "")) for o in real}
        now = time.time() * 1000

        # Guard against mass-vanish (API glitch returning 0 while we track many)
        mass_vanish = len(real) == 0 and len(self.active) >= 3
        if mass_vanish and now - self._last_vanish_alert_at > 60000:
            self._last_vanish_alert_at = now
            self._alert(
                f"Reconcile: exchange shows 0 orders but we track {len(self.active)} — "
                "suspected API glitch, skipping prune this cycle."
            )

        # Prune tracked orders not on the book (require 2 consecutive misses)
        pruned = 0
        if not mass_vanish:
            for oid, ao in list(self.active.items()):
                if oid in real_ids:
                    ao.gone_recon = 0
                    continue
                if now - ao.placed_at <= PRUNE_GRACE_MS:
                    continue
                ao.gone_recon += 1
                if ao.gone_recon >= 2:
                    del self.active[oid]
                    pruned += 1

        # Map real orders to levels, trim duplicates, adopt orphans
        occupied: set[int] = set()
        trimmed = 0
        adopted = 0
        for o in real:
            px = float(o.get("price", 0))
            if not px or sp <= 0:
                continue
            idx = round((px - lvl0) / sp)
            if idx < idx_lo or idx >= idx_hi:
                continue

            if idx not in occupied:
                occupied.add(idx)
                oid = str(o.get("order_id", ""))
                side_val = o.get("side", "sell")
                side = OrderSide.BUY if str(side_val).lower() == "buy" else OrderSide.SELL
                if oid not in self.active and not any(ao.level_index == idx for ao in self.active.values()):
                    closing = is_recovery or (side == OrderSide.SELL if self.config.mode != "short" else side == OrderSide.BUY)
                    try:
                        self._ex.adopt_order(
                            order_id=oid,
                            market_id=self.config.market_id,
                            level_index=idx,
                            side=side,
                            price=px,
                            size_base=self.config.size_base,
                        )
                    except Exception:
                        pass
                    self.active[oid] = _ActiveOrder(
                        level_index=idx,
                        side=side,
                        price=px,
                        opening=not closing,
                        recovery=is_recovery,
                    )
                    adopted += 1
            else:
                # Duplicate on same level — cancel the extra
                oid = str(o.get("order_id", ""))
                try:
                    await self._ex.cancel_order_by_id(self.config.market_id, oid)
                    self.active.pop(oid, None)
                    trimmed += 1
                except Exception:
                    pass

        if is_recovery:
            self._recovery_occupied = occupied

        if pruned or trimmed or adopted:
            self._alert(
                f"Reconcile: exchange {len(real)} orders; "
                f"pruned {pruned}, trimmed {trimmed}, adopted {adopted}."
            )
            self._notify_change()

    def _start_reconcile_timer(self) -> None:
        """Start the periodic reconciliation task."""
        if self._recon_task and not self._recon_task.done():
            return
        self._recon_task = asyncio.ensure_future(self._reconcile_loop())

    def _stop_reconcile_timer(self) -> None:
        """Stop the periodic reconciliation task."""
        if self._recon_task and not self._recon_task.done():
            self._recon_task.cancel()
            self._recon_task = None

    async def _reconcile_loop(self) -> None:
        """Background reconciliation loop."""
        while True:
            try:
                await asyncio.sleep(RECONCILE_SEC)
                await self.reconcile_open_orders()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Reconcile loop error: %s", exc)

    # ------------------------------------------------------------------
    # Internal: close with confirm
    # ------------------------------------------------------------------

    async def _close_with_confirm(self, market_id: int) -> bool:
        """Close position and confirm it's actually gone. Retry up to 3 times."""
        pos = self._ex.get_position(market_id)
        if not pos or pos.size_base == 0:
            try:
                await self._ex.close_position(market_id)
            except Exception:
                pass
            return True

        for attempt in range(1, _CLOSE_CONFIRM_RETRIES + 1):
            try:
                await self._ex.close_position(market_id)
            except Exception as exc:
                self._alert(f"Close position failed: {exc}")

            # Wait for adapter's position poll to reflect it
            for _ in range(int(_CLOSE_CONFIRM_WAIT_SEC)):
                await asyncio.sleep(1.0)
                pos = self._ex.get_position(market_id)
                if not pos or pos.size_base == 0:
                    self._alert("Position confirmed closed.")
                    return True

            if attempt < _CLOSE_CONFIRM_RETRIES:
                self._alert(f"Position still open after attempt {attempt}, retrying...")

        self._alert("Failed to close position after 3 attempts — handle manually!")
        return False

    # ------------------------------------------------------------------
    # Internal: risk computation
    # ------------------------------------------------------------------

    def _recompute_risk(self) -> None:
        """Recompute risk metrics from current grid config."""
        if not self.grid or not self.config:
            return
        mid = (self.config.lower + self.config.upper) / 2
        notional = self.grid.count * self.config.size_base * mid
        self.risk = {
            "leverage": self.config.leverage,
            "notional": _round2(notional),
            "required_margin": _round2(notional / self.config.leverage),
            "per_rung_profit": _round2(self.grid.spacing * self.config.size_base),
            "spacing_pct": _round2((self.grid.spacing / mid) * 100),
        }

    # ------------------------------------------------------------------
    # Internal: health
    # ------------------------------------------------------------------

    def _health(self) -> GridHealth:
        """Per-exchange health classification."""
        ok_age = (time.time() * 1000 - self._ex.last_ok_at) if isinstance(self._ex.last_ok_at, (int, float)) and self._ex.last_ok_at > 0 else 0.0
        recent_fail = self._last_fail_at and (time.time() * 1000 - self._last_fail_at < 60000)
        paused = self._refill_paused_until and time.time() * 1000 < self._refill_paused_until

        if not self.running and not self.config:
            status, reason = "idle", "Not running"
        elif paused:
            status, reason = "error", "Auto-refill paused (burst cancellations)"
        elif self._ex.data_source == "synthetic":
            status, reason = "warn", "Synthetic data (not connected to real exchange)"
        elif ok_age > 30000:
            status, reason = "error", f"Exchange data {int(ok_age / 1000)}s stale"
        elif recent_fail:
            status, reason = "warn", f"Order failures in last minute: {self._place_fails}"
        else:
            status, reason = "ok", "Running normally"

        return GridHealth(
            status=status,
            reason=reason,
            data_source=self._ex.data_source,
            last_ok_age_ms=ok_age,
            price_stale=False,
            place_fails=self._place_fails,
            exchange_open_orders=self._exchange_open_orders,
        )

    # ------------------------------------------------------------------
    # Internal: alerts and persistence
    # ------------------------------------------------------------------

    def _alert(self, message: str) -> None:
        """Record an alert message."""
        self.alerts.insert(0, {"t": time.time() * 1000, "message": message})
        if len(self.alerts) > _MAX_ALERTS_HISTORY:
            self.alerts.pop()

    def _notify_change(self) -> None:
        """Notify the persistence layer that durable state changed."""
        if self._on_change:
            try:
                self._on_change(self.snapshot())
            except Exception:
                pass

    def set_on_change(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Set the persistence callback."""
        self._on_change = callback
