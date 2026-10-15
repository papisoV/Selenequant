"""Strategy Runner — continuously executes deployed strategies against live market data.

Subscribes to kline events from MarketDataService, feeds them through
StrategyRuntime, and converts resulting Signals into paper orders via
PaperExecutionEngine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ORDER_SIZE_USDT = 20.0
MAX_POSITION_SIZE_USDT = 5000.0
MAX_SIGNAL_HISTORY = 50
WARMUP_KLINE_COUNT = 200


def _kline_label_ms(event: Any) -> int:
    """Return the kline open/label timestamp for use as signal time.

    Charts label candles by their open time (e.g. the "06:00 candle" spans
    06:00–07:00).  Strategy fills should be timestamped with this label so
    they visually align with the candle that produced the signal.
    """
    ts: int = getattr(event, "timestamp", 0)
    return ts if ts else int(time.time() * 1000)


def _config_has_rules(cfg: dict[str, Any]) -> bool:
    """Return True if a strategy config contains non-empty trading rules."""
    entry = cfg.get("entry_rules") or {}
    exit_ = cfg.get("exit_rules") or {}
    return bool(entry) or bool(exit_)


# Restore failure classification (absorbed from QD's trading-worker
# desired-state pattern): permanent failures drop the deployment, everything
# else — including unknown errors — is transient (fail-closed) and stays
# queued for retry, so a startup network/DB hiccup never silently kills a
# deployment.
_PERMANENT_RESTORE_MARKERS = (
    "already running",
    "is already deployed",
    "cannot deploy",
    "compilation failed",
    "blocked:",
)
_RESTORE_RETRY_INTERVAL = 30.0
_RESTORE_MAX_ATTEMPTS = 5


def _classify_restore_failure(err: str) -> str:
    lowered = err.lower()
    if any(m in lowered for m in _PERMANENT_RESTORE_MARKERS):
        return "permanent"
    return "transient"


def _restore_config_deployable(cfg: dict[str, Any]) -> bool:
    """Mirror deploy()'s rules check, including its alpha/template exemption.

    deploy() skips the entry/exit-rules requirement for alpha formulas and
    Python templates; restore must not reject those configs, or alpha
    deployments can never survive a restart.
    """
    if _config_has_rules(cfg):
        return True
    source = cfg.get("source", "")
    return bool(source.startswith(("alpha:", "template:")) and source != "template:")


class LiquidityContextProvider:
    """Precompute a LiquidityContext for the tiered trailing stop.

    The tiered tracker's on_tick is a microsecond-level hot path, so the
    VPVR profile must be computed here (per-kline, throttled) and handed
    over as a plain snapshot. Any failure degrades to None (legacy pure
    peak-pct stop), never raises.
    """

    _REFETCH_SECONDS = 60.0
    _POC_FRACTION = 0.5  # level counts as HVN at >= 50% of POC volume

    def __init__(self, market: Any) -> None:
        self._market = market
        # Round-number granularity (e.g. 1000.0 for BTC) from env config;
        # 0 disables round-level avoidance.
        self._round_step = float(
            os.environ.get("SELENE_LIQUIDITY_ROUND_STEP", "0") or 0
        )
        self._cache: dict[str, tuple[float, Any]] = {}

    def get(self, symbol: str, current_price: float) -> Any | None:
        """Return a LiquidityContext, or None on no data / any error."""
        try:
            from selene_space_paper.tiered_trailing_stop import LiquidityContext

            now = time.monotonic()
            cached = self._cache.get(symbol)
            if cached is None or now - cached[0] > self._REFETCH_SECONDS:
                profile = self._market.get_vpvr(symbol)
                self._cache[symbol] = (now, profile)
            else:
                profile = cached[1]
            if profile is None:
                return None

            levels = [lv for lv in profile.levels if lv.total_volume > 0]
            if not levels:
                return None
            poc_vol = max(lv.total_volume for lv in levels)
            hvns = [
                lv.price
                for lv in levels
                if lv.total_volume >= poc_vol * self._POC_FRACTION
            ]
            # Nearest 3 HVN levels strictly below current price, deepest last.
            below = sorted((p for p in hvns if p < current_price), reverse=True)[:3]
            round_step = self._round_step
            return LiquidityContext(
                hvn_prices=tuple(below),
                round_step=round_step,
            )
        except Exception:
            logger.debug(
                "LiquidityContextProvider failed for %s", symbol, exc_info=True
            )
            return None


class _PythonTemplateRuntime:
    """Runtime wrapper for BacktestStrategyTemplate-based strategies.

    Provides the same interface as StrategyRuntime for Python template strategies.
    These strategies use BacktestStrategyTemplate.on_bar() instead of compiled YAML rules.
    """

    def __init__(self, strategy: Any, symbol: str, interval: str):
        self._strategy = strategy
        self._symbol = symbol
        self._interval = interval
        self._bar_count = 0
        self._position = "flat"

    @property
    def position(self) -> str:
        return self._position

    @property
    def bar_count(self) -> int:
        return self._bar_count

    def on_kline(self, event: Any) -> Any:
        """Process a kline event and return signal if any."""
        self._bar_count += 1
        try:
            # BacktestStrategyTemplate.on_kline expects a KlineEvent, not a dict.
            # Pass the event through directly — the strategy reads
            # event.open/high/low/close/volume/timestamp itself.
            signal = self._strategy.on_kline(event)

            # Update position tracking
            if signal is not None:
                side = getattr(signal, "side", None)
                if side is not None:
                    side_val = side.value if hasattr(side, "value") else str(side)
                    if "buy" in side_val.lower():
                        self._position = "long"
                    elif "sell" in side_val.lower():
                        self._position = "flat"

            return signal
        except Exception as e:
            logger.warning("Python template on_kline error: %s", e)
            return None


@dataclass
class RunnerSlot:
    """In-flight state for a single strategy deployment."""

    deployment_id: str
    strategy_id: str
    account_id: str
    symbol: str
    interval: str
    runtime: Any  # StrategyRuntime
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    last_signal_ts: int = 0
    signals_emitted: int = 0
    orders_placed: int = 0
    errors: int = 0
    # Watchdog 对账修正计数 —— 此前修正只进日志,用户无从得知系统纠正过什么。
    # 暴露到 get_slot_status 供 UI 显示(透明性:修正行为必须可见)。
    position_corrections: int = 0
    last_correction_ts: int = 0
    last_correction_delta: float = 0.0
    signal_history: list[dict[str, Any]] = field(default_factory=list)
    # Tracked net base-asset position (positive=long, negative=short) for
    # the LiveDecisionPipeline trailing-stop tracker. Maintained locally
    # because the paper/live engine may not expose a sync get_position().
    net_position: float = 0.0
    # Optimistic qty recorded at _execute_signal time, keyed by
    # (symbol, deployment_id). The fill callback pops this entry to
    # OVERWRITE (not add) net_position with the authoritative fill qty,
    # so optimistic + fill = single-counted (not doubled).
    _last_optimistic_qty: dict[tuple[str, str], float] = field(default_factory=dict)
    # Per-(symbol, deployment_id) lock guarding the optimistic-update +
    # fill-callback pair against concurrent fills clobbering net_position.
    # Keyed by (symbol, deployment_id) — same key as _last_optimistic_qty.
    _fill_locks: dict[tuple[str, str], asyncio.Lock] = field(default_factory=dict)


class StrategyRunner:
    """Manages multiple concurrently running strategy deployments.

    Lifecycle:
        1. ``start()`` — restores persisted deployments and hooks into kline events.
        2. ``deploy(deployment_id, strategy_config, account_id)`` — adds a new slot.
        3. ``stop_deployment(deployment_id)`` — removes a slot.
        4. ``stop()`` — tears down all slots and saves state.
    """

    def __init__(
        self,
        paper_engine: Any,
        market_service: Any,
        *,
        state_dir: Path | None = None,
        order_size_usdt: float = DEFAULT_ORDER_SIZE_USDT,
        live_engine: Any | None = None,
        shadow_engine: Any | None = None,
        mode_provider: Any | None = None,
        live_pipeline: Any | None = None,
        dual_leg_executor: Any | None = None,
    ) -> None:
        self._paper = paper_engine
        self._market = market_service
        self._live = live_engine
        self._shadow = shadow_engine
        # mode_provider is a callable returning current ExecutionMode value string
        # ("paper"/"shadow"/"confirm"/"limited_auto"/"full_auto"). If None,
        # defaults to "paper".
        self._mode_provider = mode_provider
        # Optional LiveDecisionPipeline — when set and mode != paper, the
        # signal runs through the 8-stage pipeline (dedup/throttle/risk/limits)
        # before reaching the execution engine.
        self._live_pipeline = live_pipeline
        # Optional funding-arb dual-leg executor (spot + perp clients). When
        # set, funding_arb signals in live modes route through BOTH legs via
        # DualLegExecutor instead of a single spot order. None → single-order
        # path (paper/shadow/observing behavior unchanged).
        self._dual_leg_executor = dual_leg_executor
        self._slots: dict[str, RunnerSlot] = {}
        # H11/H12: per-deployment in-flight task tracker. _execute_signal and
        # _broadcast_status are scheduled via asyncio.ensure_future; without
        # tracking their exceptions are silently swallowed (H11) and they keep
        # running after stop_deployment (H12). Each task auto-discards itself
        # from the set on completion (success / exception / cancel).
        self._slot_tasks: dict[str, set[asyncio.Task[None]]] = {}
        self._state_dir = state_dir or Path.home() / ".selenespace" / "runner"
        self._order_size = order_size_usdt
        self._running = False
        self._kline_registered = False
        # Precomputed LiquidityContext feed for the tiered trailing stop
        # (VPVR HVN extraction, throttled; None on any failure).
        self._liquidity_provider = LiquidityContextProvider(market_service)
        self._signal_callbacks: list[Any] = []  # Callbacks for signal events
        self._serenity_filter: Any = (
            None  # SerenityScoreCache, set via set_serenity_filter()
        )
        self._serenity_filter_mode: str = "soft"  # "off", "soft", "hard"
        self._event_guard: Any = None  # EventGuard, set via set_event_guard()
        # Per-symbol → most-recent-order deployment_id. Used to route async
        # fill events back to the slot that placed the order, since the Fill
        # model does not carry a deployment_id.
        self._last_order_slot: dict[str, str] = {}
        # Per-symbol FIFO queue of deployment_ids awaiting fills (paper path
        # fallback when fill.client_order_id is absent). The fill callback
        # pops the oldest deployment_id, so concurrent orders on the same
        # symbol no longer silently lose fills when _last_order_slot is
        # overwritten by a later order.
        self._last_order_slot_queue: dict[str, Any] = {}
        # Whether on_fill callbacks have been registered on the engines.
        self._fill_cb_registered: bool = False
        # Background reconciliation task (independent fill source watchdog).
        self._reconcile_task: asyncio.Task[None] | None = None
        # US-004: consecutive divergent rounds per deployment_id. The watchdog
        # only rewrites slot.net_position after >=2 consecutive divergent
        # rounds with no in-flight order (protects correct in-flight accounting).
        self._diverge_streak: dict[str, int] = {}
        # Desired-state restore: transient failures stay queued here for
        # retry (dep_id -> state-file entry) with per-deployment attempt
        # counts, so a startup hiccup doesn't silently lose deployments.
        self._pending_restores: dict[str, dict[str, Any]] = {}
        self._restore_attempts: dict[str, int] = {}
        self._restore_retry_task: asyncio.Task[None] | None = None

    def set_engines(
        self,
        *,
        live_engine: Any | None = None,
        shadow_engine: Any | None = None,
        mode_provider: Any | None = None,
        live_pipeline: Any | None = None,
        dual_leg_executor: Any | None = None,
    ) -> None:
        """Inject/replace execution engines after construction.

        Called from lifespan once LiveEngine/ShadowEngine are initialized
        (they are created after StrategyRunner in the startup sequence).
        """
        if live_engine is not None:
            self._live = live_engine
        if shadow_engine is not None:
            self._shadow = shadow_engine
        if mode_provider is not None:
            self._mode_provider = mode_provider
        if live_pipeline is not None:
            self._live_pipeline = live_pipeline
        if dual_leg_executor is not None:
            self._dual_leg_executor = dual_leg_executor

        # Register an on_fill callback on live / shadow engines so the
        # trailing-stop tracker is seeded/cleared from the actual fill event
        # (not the optimistic place_order return). Paper engine fills
        # synchronously inside place_order, so the callback fires immediately
        # there too — but the call is idempotent (notify_fill on BUY resets
        # peak; multiple BUYs just refresh the anchor).
        if not self._fill_cb_registered:
            for eng in (self._live, self._shadow):
                if eng is not None and hasattr(eng, "on_fill"):
                    try:
                        eng.on_fill(self._on_fill_event)
                    except Exception:
                        logger.warning(
                            "Failed to register on_fill callback on %s",
                            type(eng).__name__,
                            exc_info=True,
                        )
            self._fill_cb_registered = True

    def _on_fill_event(self, fill: Any) -> None:
        """Handle an async Fill event from the execution engine.

        Routes the fill back to the slot that placed the order (by
        symbol → most-recent deployment_id), updates that slot's
        net_position, and feeds the trailing-stop tracker via
        notify_fill. Safe to call for paper / shadow / live engines —
        they all emit Fill objects with the same field shape.
        """
        try:
            symbol = getattr(fill, "symbol", "") or ""
            side = (getattr(fill, "side", "") or "").lower()
            price = float(getattr(fill, "price", 0.0) or 0.0)
            # Fill.quantity is USDT notional; convert to base-asset qty.
            qty_notional = float(getattr(fill, "quantity", 0.0) or 0.0)
            if price > 0:
                qty_base = qty_notional / price
            else:
                qty_base = 0.0
            if not symbol or not side or qty_base <= 0:
                return

            # Prefer precise routing via client_order_id (passed through from
            # LiveEngine.place_order → Binance newClientOrderId → fill event).
            # Fall back to symbol+recency when client_order_id is absent (paper
            # engine sync fills, or older exchange SDK versions).
            client_order_id = str(getattr(fill, "client_order_id", "") or "")
            deployment_id: str | None = None
            if client_order_id and "-" in client_order_id:
                # Convention: "{deployment_id}-{ts_ms}". The trailing segment
                # must be purely numeric to count as a timestamp suffix —
                # this tolerates deployment_ids that themselves contain hyphens
                # (e.g. "dep-live-abc-1234567890" → "dep-live-abc") and rejects
                # malformed suffixes like "dep-None" (when signal_ts_ms was None).
                parts = client_order_id.rsplit("-", 1)
                if len(parts) == 2 and parts[0] and parts[1].isdigit():
                    deployment_id = parts[0]
            if deployment_id is None:
                # Paper-path FIFO fallback: pop oldest deployment_id from the
                # per-symbol queue. This prevents silent fill-loss when
                # _last_order_slot[symbol] was overwritten by a concurrent
                # order on the same symbol before this fill arrived.
                q = self._last_order_slot_queue.get(symbol)
                if q:
                    deployment_id = q.popleft()
            if deployment_id is None:
                deployment_id = self._last_order_slot.get(symbol)
            if deployment_id is None:
                # No tracked order for this symbol — nothing to route.
                return
            slot = self._slots.get(deployment_id)
            if slot is None:
                return

            # OVERWRITE net_position with authoritative fill qty (was `+=`,
            # which doubled the optimistic update done in _execute_signal).
            # Pop the optimistic entry so a late duplicate fill does not
            # double-subtract. If no optimistic entry exists (e.g. fill
            # arrived before _execute_signal stored it), prev=0 → behaves
            # as a plain assignment of signed_qty.
            signed_qty = qty_base if side == "buy" else -qty_base
            key = (symbol, deployment_id)
            prev = slot._last_optimistic_qty.pop(key, 0.0)
            slot.net_position = slot.net_position - prev + signed_qty

            # Feed the trailing-stop tracker.
            if self._live_pipeline is not None:
                try:
                    self._live_pipeline.notify_fill(symbol, price, side)
                    # v017-frontend: feed tiered tracker too
                    if hasattr(self._live_pipeline, "notify_fill_tiered"):
                        self._live_pipeline.notify_fill_tiered(
                            symbol, price, qty_base, side
                        )
                except Exception:
                    logger.debug("notify_fill failed for %s", symbol, exc_info=True)
        except Exception:
            logger.debug("on_fill_event handler failed", exc_info=True)

    async def _reconcile_positions(self) -> None:
        """Independent-source reconciliation watchdog (defense-in-depth).

        Every 5s, polls an INDEPENDENT position source (NOT the callback-written
        ledger, which shares the same race as the callback itself):
          - live modes → exchange positionRisk via engine._client.get_positions
          - paper mode → engine.get_fills(account_id) direct fill sum
        If slot.net_position diverges from the independent-source sum by
        >1% for >5s, log a structured error. Catches residual races where
        the fill callback missed or double-fired.
        """
        while self._running:
            try:
                await asyncio.sleep(5.0)
                for slot in list(self._slots.values()):
                    await self._reconcile_one_slot(slot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("reconcile loop error", exc_info=True)

    async def _reconcile_one_slot(self, slot: RunnerSlot) -> None:
        """One watchdog round for one slot: poll → diverge check → maybe correct.

        US-004 upgrade from log-only: after >=2 consecutive divergent rounds
        AND no in-flight order, rewrite slot.net_position from the independent
        source. In-flight (an optimistic qty awaiting its fill callback) means
        the callback will OVERWRITE net_position with the authoritative fill —
        correcting now would clobber correct accounting, so report only.
        """
        try:
            independent = await self._query_independent_net_position(slot)
        except Exception:
            logger.debug(
                "reconcile: independent source unavailable for %s",
                slot.deployment_id,
                exc_info=True,
            )
            return
        if independent is None:
            return
        tracked = slot.net_position
        if independent == 0.0:
            diverge_pct = 0.0 if tracked == 0.0 else 1.0
        else:
            diverge_pct = abs(tracked - independent) / abs(independent)
        if diverge_pct <= 0.01:
            self._diverge_streak[slot.deployment_id] = 0
            return
        streak = self._diverge_streak.get(slot.deployment_id, 0) + 1
        self._diverge_streak[slot.deployment_id] = streak
        has_inflight = bool(
            slot._last_optimistic_qty.get((slot.symbol, slot.deployment_id))
        )
        logger.error(
            "position_divergence divergence_pct=%f symbol=%s "
            "deployment_id=%s tracked=%f independent=%f "
            "source=exchange_api streak=%d in_flight=%s corrected=false",
            diverge_pct,
            slot.symbol,
            slot.deployment_id,
            tracked,
            independent,
            streak,
            has_inflight,
        )
        if streak >= 2 and not has_inflight:
            slot.net_position = independent
            self._diverge_streak[slot.deployment_id] = 0
            slot.position_corrections += 1
            slot.last_correction_ts = int(time.time() * 1000)
            slot.last_correction_delta = independent - tracked
            logger.warning(
                "position_corrected divergence_pct=%f symbol=%s "
                "deployment_id=%s old=%f new=%f source=exchange_api",
                diverge_pct,
                slot.symbol,
                slot.deployment_id,
                tracked,
                independent,
            )

    def _sum_independent_fills(self, slot: RunnerSlot) -> float | None:
        """Sum signed fill qty from the paper engine's get_fills for a slot.

        Paper-mode independent source for the watchdog (live modes read
        positionRisk via _query_independent_net_position instead).
        Returns None when no paper source is available (skip reconcile).
        """
        mode = self._resolve_mode()
        engine = self._select_engine(mode)
        if engine is None:
            return None
        try:
            getter = getattr(engine, "get_fills", None)
            if getter is None:
                return None
            fills = getter(slot.account_id) or []
        except Exception:
            return None
        total = 0.0
        for f in fills:
            f_symbol = getattr(f, "symbol", "") or ""
            if f_symbol and f_symbol != slot.symbol:
                continue
            f_client_id = str(getattr(f, "client_order_id", "") or "")
            f_dep: str | None = None
            if f_client_id and "-" in f_client_id:
                parts = f_client_id.rsplit("-", 1)
                if len(parts) == 2 and parts[0] and parts[1].isdigit():
                    f_dep = parts[0]
            if f_dep is not None and f_dep != slot.deployment_id:
                continue
            f_side = (getattr(f, "side", "") or "").lower()
            f_price = float(getattr(f, "price", 0.0) or 0.0)
            f_qty_notional = float(getattr(f, "quantity", 0.0) or 0.0)
            if f_price <= 0:
                continue
            f_qty_base = f_qty_notional / f_price
            if f_qty_base <= 0:
                continue
            total += f_qty_base if f_side == "buy" else -f_qty_base
        return total

    async def _query_independent_net_position(self, slot: RunnerSlot) -> float | None:
        """Query net base-asset position from an INDEPENDENT source.

        Live: exchange positionRisk via engine._client.get_positions(symbol)
        (adapter strips "/" and hits /fapi/v2/positionRisk). positionAmt is
        already signed; net (BOTH) mode returns one entry, hedge mode returns
        LONG+SHORT entries — summing gives the net exposure either way.
        Paper: reuse _sum_independent_fills (engine.get_fills direct read).
        Returns None when no source is available or any error occurs (skip).
        """
        mode = self._resolve_mode()
        engine = self._select_engine(mode)
        if engine is None:
            return None
        if mode == "paper":
            return self._sum_independent_fills(slot)
        client = getattr(engine, "_client", None)
        getter = getattr(client, "get_positions", None)
        if getter is None:
            return None
        try:
            positions = await getter(slot.symbol)
        except Exception:
            return None
        if not isinstance(positions, list):
            return None
        total = 0.0
        for p in positions:
            if not isinstance(p, dict):
                continue
            try:
                total += float(p.get("positionAmt", 0) or 0)
            except (TypeError, ValueError):
                return None
        return total

    def _resolve_mode(self) -> str:
        """Return the current execution mode string. Defaults to 'paper'."""
        if self._mode_provider is None:
            return "paper"
        try:
            mode = self._mode_provider()
            return mode if mode else "paper"
        except Exception:
            logger.warning("mode_provider failed; defaulting to 'paper'", exc_info=True)
            return "paper"

    def _select_engine(self, mode: str) -> Any | None:
        """Pick the execution engine for the given mode.

        - paper              → PaperExecutionEngine
        - shadow             → ShadowExecutionEngine
        - confirm/limited_auto/full_auto → LiveExecutionEngine
        """
        if mode == "paper":
            return self._paper
        if mode == "shadow":
            return self._shadow
        # confirm / limited_auto / full_auto all use LiveEngine
        return self._live

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_deployments(self) -> list[str]:
        return list(self._slots.keys())

    def get_slot_status(self, deployment_id: str) -> dict[str, Any] | None:
        slot = self._slots.get(deployment_id)
        if slot is None:
            return None
        return {
            "deployment_id": slot.deployment_id,
            "strategy_id": slot.strategy_id,
            "account_id": slot.account_id,
            "symbol": slot.symbol,
            "interval": slot.interval,
            "position": slot.runtime.position,
            "bar_count": slot.runtime.bar_count,
            "signals_emitted": slot.signals_emitted,
            "orders_placed": slot.orders_placed,
            "errors": slot.errors,
            "last_signal_ts": slot.last_signal_ts,
            # 对账修正可见化:用户需知道系统纠正过什么(此前只进日志)
            "position_corrections": slot.position_corrections,
            "last_correction_ts": slot.last_correction_ts,
            "last_correction_delta": slot.last_correction_delta,
            "recent_signals": slot.signal_history[-20:],
        }

    def get_signal_history(self, deployment_id: str) -> list[dict[str, Any]]:
        slot = self._slots.get(deployment_id)
        if slot is None:
            return []
        return list(slot.signal_history)

    def funding_arb_slots(self) -> list[dict[str, Any]]:
        """Read-only view of funding_arb deployment slots for flatten closures.

        Only slots with strategy_id == "funding_arb" are returned. Each entry
        carries enough for the flatten closure to derive the perp symbol by
        venue and size the leg orders. Read-only — never mutates slot state.
        """
        result: list[dict[str, Any]] = []
        for slot in self._slots.values():
            if slot.strategy_id != "funding_arb":
                continue
            result.append(
                {
                    "deployment_id": slot.deployment_id,
                    "account_id": slot.account_id,
                    "symbol": slot.symbol,
                    "strategy_id": slot.strategy_id,
                    "net_position": float(slot.net_position),
                }
            )
        return result

    def on_signal(self, callback: Any) -> None:
        """Register a callback to be invoked when strategy signals are emitted.

        Callback signature: callback(deployment_id: str, signal: Signal)
        """
        self._signal_callbacks.append(callback)

    def set_serenity_filter(self, cache: Any, mode: str = "soft") -> None:
        """Set Serenity score cache for regime filtering.

        Args:
            cache: SerenityScoreCache instance (or None to disable).
            mode: "off" (no filter), "soft" (reduce strength), "hard" (block trade).
        """
        self._serenity_filter = cache
        self._serenity_filter_mode = mode
        if cache is not None:
            logger.info("Serenity filter enabled: mode=%s", mode)
        else:
            logger.info("Serenity filter disabled")

    def set_event_guard(self, guard: Any) -> None:
        """Set EventGuard for macro event position scaling.

        Args:
            guard: EventGuard instance (or None to disable).
        """
        self._event_guard = guard
        if guard is not None:
            logger.info("EventGuard enabled: macro event position scaling active")
        else:
            logger.info("EventGuard disabled")

    async def start(self) -> None:
        self._running = True
        self._ensure_kline_hook()
        await self._restore_state()
        if self._pending_restores and (
            self._restore_retry_task is None or self._restore_retry_task.done()
        ):
            self._restore_retry_task = asyncio.ensure_future(self._restore_retry_loop())
        # Launch independent-source reconciliation watchdog. Polls an
        # uncorrelated fill source (exchange API for live, paper engine
        # get_fills() for paper) every 5s; logs + emits a structured error
        # when net_position diverges from the independent source by >1%.
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.ensure_future(self._reconcile_positions())
        logger.info(
            "StrategyRunner started, %d deployments restored",
            len(self._slots),
        )

    async def stop(self) -> None:
        self._running = False
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconcile_task = None
        if self._restore_retry_task is not None:
            self._restore_retry_task.cancel()
            try:
                await self._restore_retry_task
            except (asyncio.CancelledError, Exception):
                pass
            self._restore_retry_task = None
        self._pending_restores.clear()
        self._restore_attempts.clear()
        self._save_state()
        self._slots.clear()
        logger.info("StrategyRunner stopped")

    async def _check_regime_guard(self, strategy_config: dict[str, Any]) -> str | None:
        """Deploy-time regime compatibility check. Returns BLOCKED error or None.

        Fail-open: alpha/template sources, unknown strategy types, or any
        regime-probe failure all pass through (warning logged, deploy allowed).
        """
        source = strategy_config.get("source", "")
        if source.startswith(("alpha:", "template:")):
            return None
        raw_type = strategy_config.get("type")
        try:
            from selene_space_types.strategy import StrategyType

            strategy_type = StrategyType(raw_type)
        except (ValueError, TypeError):
            return None
        try:
            from app.core.regime_guard import get_market_state
            from selene_space_security.guardrails.regime_mismatch import (
                RegimeMismatchDetector,
            )

            symbols = strategy_config.get("symbols") or []
            symbol = symbols[0] if symbols else "BTC/USDT"
            interval = strategy_config.get("interval") or "1h"
            state = await get_market_state(symbol, interval)
            if state is None:
                return None
            alert = RegimeMismatchDetector().check_mismatch(
                strategy_type,
                state.regime,
                strategy_name=strategy_config.get("name"),
                trend_strength=state.trend_strength,
                volatility=state.volatility,
            )
        except Exception:
            logger.warning(
                "Regime guard errored for %s; failing open",
                strategy_config.get("id", "?"),
                exc_info=True,
            )
            return None
        if alert is None:
            return None
        if alert.severity.value == "dangerous":
            return (
                f"BLOCKED: {alert.message} 当前行情不适配该策略类型，"
                "请换策略或等行情回归后重试。"
            )
        logger.warning("Regime guard warning on deploy: %s", alert.message)
        return None

    async def deploy(
        self,
        deployment_id: str,
        strategy_config: dict[str, Any],
        account_id: str,
    ) -> str | None:
        """Compile and activate a strategy deployment. Returns error string or None."""
        if deployment_id in self._slots:
            return f"Deployment {deployment_id} already running"

        # Check if this is a Python template strategy (source: "template:xxx")
        source = strategy_config.get("source", "")
        template_id = None
        if source.startswith("template:"):
            template_id = source.replace("template:", "")

        # Alpha and Python template strategies don't need YAML rules
        if source.startswith("alpha:") or (
            template_id and not _config_has_rules(strategy_config)
        ):
            pass
        elif not _config_has_rules(strategy_config):
            return (
                "Cannot deploy: entry_rules and exit_rules are empty. "
                "The strategy has no trading logic — save complete rules first."
            )

        strategy_id = strategy_config.get("id", "")
        existing = [s for s in self._slots.values() if s.strategy_id == strategy_id]
        if existing:
            dep_ids = ", ".join(s.deployment_id for s in existing)
            return f"Strategy {strategy_id} is already deployed ({dep_ids}). Stop existing deployment(s) first."

        regime_err = await self._check_regime_guard(strategy_config)
        if regime_err:
            return regime_err

        if account_id in ("paper-default", "auto", ""):
            new_account_id = await self._create_strategy_account(strategy_config)
            if new_account_id:
                account_id = new_account_id

        # Handle alpha formula strategies
        if source.startswith("alpha:"):
            return await self._deploy_alpha_formula(
                deployment_id, strategy_config, account_id
            )

        # Handle Python template strategies
        if template_id:
            return await self._deploy_python_template(
                deployment_id, strategy_config, account_id, template_id
            )

        # Standard YAML-based strategy deployment
        return await self._deploy_yaml_strategy(
            deployment_id, strategy_config, account_id
        )

    async def _deploy_yaml_strategy(
        self,
        deployment_id: str,
        strategy_config: dict[str, Any],
        account_id: str,
    ) -> str | None:
        """Compile a strategy from its entry/exit rules and activate it.

        The fallback path for everything that isn't an alpha formula or a
        Python-native preset: the YAML factory templates (sma_cross, ema_cross,
        adx_trend, ...) and hand-authored rule strategies all compile here.
        """
        try:
            from selene_space_agent.tools.strategy_tools import _sanitize_config
            from selene_space_strategy.compiler import compile as compile_strategy
            from selene_space_strategy.models import EngineStrategyConfig
            from selene_space_strategy.runtime import StrategyRuntime

            cfg = dict(strategy_config)
            _sanitize_config(cfg)
            engine_config = EngineStrategyConfig.model_validate(cfg)
            # A strategy with no entry conditions can never open a position:
            # the runtime's entry test is ``conditions and all(...)`` and an
            # empty list is falsy, so ``compile`` accepts it and the deployment
            # sits flat forever without an error. Refuse it here the same way
            # the backtest path does -- a live deploy that can never trade is
            # worse than a backtest that can't.
            #
            # The "Cannot deploy" prefix is load-bearing: _classify_restore_failure
            # reads it as permanent, so a config that reaches here is dropped at
            # restore rather than retried. Without it this error would be retried
            # _RESTORE_MAX_ATTEMPTS times on every restart, forever, against a
            # condition that can never become true.
            if (
                not engine_config.parsed_entry_rules.long
                and not engine_config.parsed_entry_rules.short
            ):
                return (
                    f"Cannot deploy: strategy '{engine_config.id}' has no entry "
                    "rules, so it could never trade. This usually means the "
                    "template is listed but has no implementation."
                )
            compiled = compile_strategy(engine_config)
            runtime = StrategyRuntime(compiled, direction=engine_config.direction)
        except Exception as exc:
            logger.error(
                "Failed to compile strategy for deployment %s: %s", deployment_id, exc
            )
            return f"Compilation failed: {exc}"

        symbol = engine_config.symbols[0] if engine_config.symbols else "BTC/USDT"
        interval = engine_config.interval or "1h"

        slot = RunnerSlot(
            deployment_id=deployment_id,
            strategy_id=engine_config.id,
            account_id=account_id,
            symbol=symbol,
            interval=interval,
            runtime=runtime,
            config_snapshot=strategy_config,
        )
        self._slots[deployment_id] = slot
        self._ensure_kline_hook()

        await self._ensure_symbol_subscribed(symbol, interval)

        await self._warmup_historical_klines(slot)

        self._save_state()
        logger.info(
            "Deployed strategy %s on %s/%s → account %s (warmup bars: %d)",
            engine_config.id,
            symbol,
            interval,
            account_id,
            runtime.bar_count,
        )
        return None

    async def _deploy_alpha_formula(
        self,
        deployment_id: str,
        strategy_config: dict[str, Any],
        account_id: str,
    ) -> str | None:
        """Deploy an AI-discovered alpha formula strategy via AlphaFormulaStrategy."""
        try:
            from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy
            from selene_space_alpha.vocab import FormulaVocab, FORMULA_VOCAB

            formula_tokens = strategy_config.get("formula_tokens", [])
            if not formula_tokens:
                return "Cannot deploy alpha strategy: formula_tokens is empty"

            vocab_config = strategy_config.get("vocab", {})
            if vocab_config:
                vocab = FormulaVocab(
                    feature_names=tuple(
                        vocab_config.get("feature_names", FORMULA_VOCAB.feature_names)
                    ),
                    operator_names=tuple(
                        vocab_config.get("operator_names", FORMULA_VOCAB.operator_names)
                    ),
                )
            else:
                vocab = FORMULA_VOCAB

            strategy = AlphaFormulaStrategy(
                formula_tokens=formula_tokens,
                vocab=vocab,
                buy_threshold=strategy_config.get("buy_threshold", 0.57),
                sell_threshold=strategy_config.get("sell_threshold", 0.41),
                name=strategy_config.get("id", "alpha_formula"),
                aux_interval=strategy_config.get("aux_interval"),
            )

            symbols = strategy_config.get("symbols", ["BTC/USDT"])
            symbol = symbols[0] if symbols else "BTC/USDT"
            interval = strategy_config.get("interval", "15m")

            # AlphaFormulaStrategy implements on_kline() directly — no wrapper needed
            runtime = strategy

            slot = RunnerSlot(
                deployment_id=deployment_id,
                strategy_id=strategy_config.get("id", ""),
                account_id=account_id,
                symbol=symbol,
                interval=interval,
                runtime=runtime,
                config_snapshot=strategy_config,
            )
            self._slots[deployment_id] = slot
            self._ensure_kline_hook()

            await self._ensure_symbol_subscribed(
                symbol,
                interval,
                exchange=strategy_config.get("exchange") or "binance",
                market_type=strategy_config.get("market_type") or "spot",
            )

            # Also subscribe aux interval if needed (for TF_ features)
            aux_interval = strategy_config.get("aux_interval")
            if aux_interval:
                await self._ensure_symbol_subscribed(
                    symbol,
                    aux_interval,
                    exchange=strategy_config.get("exchange") or "binance",
                    market_type=strategy_config.get("market_type") or "spot",
                )
            await self._warmup_historical_klines(slot)

            self._save_state()
            logger.info(
                "Deployed alpha formula '%s' on %s/%s → account %s (formula: %s)",
                strategy_config.get("id", ""),
                symbol,
                interval,
                account_id,
                strategy.formula_readable,
            )
            return None

        except Exception as exc:
            logger.error(
                "Failed to deploy alpha formula strategy: %s", exc, exc_info=True
            )
            return f"Alpha formula deployment failed: {exc}"

    async def _deploy_python_template(
        self,
        deployment_id: str,
        strategy_config: dict[str, Any],
        account_id: str,
        template_id: str,
    ) -> str | None:
        """Deploy a Python template strategy using BacktestStrategyTemplate pattern."""
        try:
            # Import all Python template strategy classes
            from selene_space_strategy.templates.presets.bb_atr_trend import (
                BBATRTrendStrategy,
            )
            from selene_space_strategy.templates.presets.bb_v3 import BBV3Strategy
            from selene_space_strategy.templates.presets.calendar_effect import (
                CalendarEffectStrategy,
            )
            from selene_space_strategy.templates.presets.dca_grid import DCAGridStrategy
            from selene_space_strategy.templates.presets.dmi_delta import (
                DMIDeltaStrategy,
            )
            from selene_space_strategy.templates.presets.ema_triple_cross import (
                EMATripleCrossStrategy,
            )
            from selene_space_strategy.templates.presets.keltner_v2 import (
                KeltnerChannelV2Strategy,
            )
            from selene_space_strategy.templates.presets.ma_extended_runs import (
                MAExtendedRunsStrategy,
            )
            from selene_space_strategy.templates.presets.p_signal import PSignalStrategy
            from selene_space_strategy.templates.presets.pyramid_ma import (
                PyramidMAStrategy,
            )
            from selene_space_strategy.templates.presets.rsi_ma_delta import (
                RSIMADeltaStrategy,
            )
            from selene_space_strategy.templates.presets.stoch_supertrend import (
                STOCHSupertrendStrategy,
            )
            from selene_space_strategy.templates.presets.tema_channel import (
                TEMAChannelStrategy,
            )
            from selene_space_strategy.templates.presets.vw_ema_cross import (
                VWEMACrossoverStrategy,
            )
            from selene_space_strategy.templates.presets.vwap_fibo_bands import (
                VWAPFiboBandsStrategy,
            )
            from selene_space_strategy.templates.presets.statistical.hurst_reversion import (
                HurstReversionStrategy,
            )
            from selene_space_strategy.templates.presets.statistical.pairs_trading import (
                PairsTradingStrategy,
            )
            from selene_space_strategy.templates.presets.derivatives.funding_arb import (
                FundingArbStrategy,
            )
            from selene_space_strategy.templates.presets.derivatives.basis_carry import (
                BasisCarryStrategy,
            )
            from selene_space_strategy.templates.presets.intraday_seasonality import (
                IntradaySeasonalityStrategy,
            )
            from selene_space_strategy.templates.presets.rebalance_premium import (
                RebalancePremiumStrategy,
            )
            from selene_space_strategy.templates.presets.tsmom_vol_target import (
                TsmomVolTargetStrategy,
            )
            from selene_space_strategy.templates.presets.cross_sectional_momentum import (
                CrossSectionalMomentumStrategy,
            )
            from selene_space_strategy.templates.presets.january_barometer import (
                JanuaryBarometerStrategy,
            )

            # Map template_id to strategy class (24 templates supported)
            TEMPLATE_CLASS_MAP = {
                "bb_atr_trend": BBATRTrendStrategy,
                "bb_v3": BBV3Strategy,
                "calendar_effect": CalendarEffectStrategy,
                "intraday_seasonality": IntradaySeasonalityStrategy,
                "rebalance_premium": RebalancePremiumStrategy,
                "tsmom_vol_target": TsmomVolTargetStrategy,
                "cross_sectional_momentum": CrossSectionalMomentumStrategy,
                "january_barometer": JanuaryBarometerStrategy,
                "dca_grid": DCAGridStrategy,
                "dmi_delta": DMIDeltaStrategy,
                "ema_triple_cross": EMATripleCrossStrategy,
                "keltner_v2": KeltnerChannelV2Strategy,
                "ma_extended_runs": MAExtendedRunsStrategy,
                "p_signal": PSignalStrategy,
                "pyramid_ma": PyramidMAStrategy,
                "rsi_ma_delta": RSIMADeltaStrategy,
                "stoch_supertrend": STOCHSupertrendStrategy,
                "tema_channel": TEMAChannelStrategy,
                "vw_ema_cross": VWEMACrossoverStrategy,
                "vwap_fibo_bands": VWAPFiboBandsStrategy,
                "hurst_reversion": HurstReversionStrategy,
                "pairs_trading": PairsTradingStrategy,
                "funding_arb": FundingArbStrategy,
                "basis_carry": BasisCarryStrategy,
            }

            strategy_class = TEMPLATE_CLASS_MAP.get(template_id)
            if strategy_class is None:
                # "template:xxx" covers two families: the Python presets in this
                # map, and the YAML factory templates (sma_cross, ema_cross,
                # adx_trend, ...) that carry entry/exit rules and compile through
                # the standard path. Only the former belong here; anything else
                # falls back to the YAML deployer instead of erroring out.
                logger.info(
                    "Template %s is not a Python preset; deploying via YAML path",
                    template_id,
                )
                return await self._deploy_yaml_strategy(
                    deployment_id, strategy_config, account_id
                )

            # Get parameters from config
            params = strategy_config.get("parameters", {})
            symbols = strategy_config.get("symbols", ["BTC/USDT"])
            symbol = symbols[0] if symbols else "BTC/USDT"
            interval = strategy_config.get("interval", "1h")
            initial_capital = float(
                os.environ.get("SELENE_PAPER_STARTING_BALANCE", "10000")
            )

            # Instantiate strategy
            strategy = strategy_class(
                symbol=symbol, initial_capital=initial_capital, **params
            )

            # Create runtime wrapper for BacktestStrategyTemplate
            runtime = _PythonTemplateRuntime(strategy, symbol, interval)

            slot = RunnerSlot(
                deployment_id=deployment_id,
                strategy_id=strategy_config.get("id", ""),
                account_id=account_id,
                symbol=symbol,
                interval=interval,
                runtime=runtime,
                config_snapshot=strategy_config,
            )
            self._slots[deployment_id] = slot
            self._ensure_kline_hook()

            await self._ensure_symbol_subscribed(symbol, interval)

            # Warmup with historical klines
            await self._warmup_historical_klines(slot)

            self._save_state()
            logger.info(
                "Deployed Python template %s on %s/%s → account %s",
                template_id,
                symbol,
                interval,
                account_id,
            )
            return None

        except Exception as exc:
            logger.error("Failed to deploy Python template %s: %s", template_id, exc)
            return f"Python template deployment failed: {exc}"

    async def _ensure_symbol_subscribed(
        self,
        symbol: str,
        interval: str,
        exchange: str = "binance",
        market_type: str = "spot",
    ) -> None:
        """Subscribe the market service to the strategy's symbol and check interval support."""
        try:
            await self._market.add_symbol(
                symbol, exchange=exchange, market_type=market_type
            )
        except Exception:
            logger.warning(
                "Failed to subscribe symbol %s for runner", symbol, exc_info=True
            )

        _intervals_raw = (
            os.environ.get("SELENE_KLINE_INTERVALS")
            or os.environ.get("SELENE_DEFAULT_INTERVAL")
            or "30m,1h"
        )
        supported = [i.strip() for i in _intervals_raw.split(",") if i.strip()]
        if interval not in supported:
            logger.warning(
                "Strategy interval '%s' is not in configured kline intervals %s. "
                "Signals will NOT fire. Set SELENE_KLINE_INTERVALS to include '%s'.",
                interval,
                supported,
                interval,
            )
        else:
            logger.info(
                "Strategy interval '%s' is supported (configured: %s).",
                interval,
                supported,
            )

    async def _create_strategy_account(
        self, strategy_config: dict[str, Any]
    ) -> str | None:
        """Create a dedicated paper account for a strategy deployment."""
        try:
            from app.core.dependencies import get_paper_account_manager
            from selene_space_paper.accounts import AccountType

            mgr = get_paper_account_manager()
            if mgr is None:
                return None

            strategy_name = strategy_config.get("name") or strategy_config.get(
                "id", "unknown"
            )
            strategy_id = strategy_config.get("id", "")
            acct = mgr.create_account(
                name=f"Strategy: {strategy_name}",
                initial_balance=float(
                    os.environ.get("SELENE_PAPER_STARTING_BALANCE", "10000")
                ),
                account_type=AccountType.STRATEGY,
                strategy_id=strategy_id,
            )
            logger.info(
                "Auto-created strategy account %s for strategy %s",
                acct.id,
                strategy_id,
            )
            return str(acct.id)
        except Exception:
            logger.warning("Failed to auto-create strategy account", exc_info=True)
            return None

    async def _warmup_historical_klines(self, slot: RunnerSlot) -> None:
        """Preload historical closed klines via REST so indicators start warm."""
        try:
            snap = slot.config_snapshot
            exchange = snap.get("exchange") or "binance"
            market_type = snap.get("market_type") or "spot"
            klines = await self._market.fetch_klines_rest(
                slot.symbol,
                exchange=exchange,
                market_type=market_type,
                interval=slot.interval,
                limit=WARMUP_KLINE_COUNT,
            )
            if not klines:
                logger.warning(
                    "No historical klines for warmup (%s/%s)",
                    slot.symbol,
                    slot.interval,
                )
            else:
                fed = 0
                for kline in klines:
                    if not getattr(kline, "closed", True):
                        continue
                    slot.runtime.on_kline(kline)
                    fed += 1

                self._reset_warmup_position_state(slot)

                logger.info(
                    "Warmup: fed %d historical klines to deployment %s (%s/%s), bar_count=%d",
                    fed,
                    slot.deployment_id,
                    slot.symbol,
                    slot.interval,
                    slot.runtime.bar_count,
                )
        except Exception:
            logger.warning(
                "Historical kline warmup failed for deployment %s",
                slot.deployment_id,
                exc_info=True,
            )

        await self._backfill_net_position(slot)

    async def _backfill_net_position(self, slot: RunnerSlot) -> None:
        """Restore the real exchange position after (re)deployment (修②).

        _reset_warmup_position_state forces flat even when the exchange holds
        a leftover position from a previous runner session — the runner then
        "trades" against a phantom flat. Backfill from the INDEPENDENT source
        (positionRisk) after warmup: non-zero → adopt it (long/short by sign,
        no entry_price/TP/SL — trailing stop manages from current price);
        zero or unavailable → stay flat silently (zero is the normal case).
        """
        try:
            independent = await self._query_independent_net_position(slot)
        except Exception:
            independent = None
        if independent is None or independent == 0.0:
            return
        slot.net_position = independent
        pos_state = "long" if independent > 0 else "short"
        if hasattr(slot.runtime, "_position"):
            slot.runtime._position = pos_state
        strat = getattr(slot.runtime, "_strategy", None)
        if strat is not None and hasattr(strat, "_position"):
            strat._position = pos_state
        logger.warning(
            "position_backfill adopted_independent_position=true symbol=%s "
            "deployment_id=%s net_position=%f position=%s "
            "semantics=runner_takes_over_leftover_position (no entry_price/TP/SL)",
            slot.symbol,
            slot.deployment_id,
            independent,
            pos_state,
        )

    def _reset_warmup_position_state(self, slot: RunnerSlot) -> None:
        """Clear any position a warmup signal may have opened.

        Warmup feeds historical bars through the strategy's full on_kline path
        (which runs get_signal/_process_signal), so a template like bb_v3 can
        ENTER a position during warmup and set TP/SL from a historical price.
        That phantom position would then gate live get_signal into the
        exit-only branch (position != flat blocks new entries), suppressing all
        live signals. Reset position/exit state while KEEPING the bar buffer so
        indicators stay warm — the first live closed bar starts from flat.
        """
        if hasattr(slot.runtime, "_position"):
            slot.runtime._position = "flat"
        strat = getattr(slot.runtime, "_strategy", None)
        if strat is None:
            return
        for attr, value in (
            ("_position", "flat"),
            ("_entry_price", 0.0),
            ("_position_fraction_at_entry", 1.0),
            ("_atr_sl_price", 0.0),
            ("_atr_tp_price", 0.0),
            ("_atr_stop_layer", 0),
            ("_last_signal_bar", -999999),
            ("_long_tp_price", 0.0),
            ("_long_sl_price", 0.0),
            ("_short_tp_price", 0.0),
            ("_short_sl_price", 0.0),
        ):
            if hasattr(strat, attr):
                setattr(strat, attr, value)

    def _schedule_slot_task(
        self,
        deployment_id: str,
        coro: "Coroutine[Any, Any, None]",
    ) -> asyncio.Task[None]:
        """Schedule a slot-scoped coroutine as a tracked background task (H11/H12).

        Used by _on_kline so in-flight _execute_signal / _broadcast_status
        tasks can be cancelled by stop_deployment_async. The task auto-discards
        itself from _slot_tasks on completion (success, exception, or cancel).
        Exceptions are logged as warnings (NOT silently swallowed).
        """
        task = asyncio.ensure_future(coro)
        tasks = self._slot_tasks.setdefault(deployment_id, set())
        tasks.add(task)

        def _on_done(t: asyncio.Task[None], did: str = deployment_id) -> None:
            self._slot_tasks.get(did, set()).discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                logger.warning("Slot task for deployment %s failed: %r", did, exc)

        task.add_done_callback(_on_done)
        return task

    def stop_deployment(self, deployment_id: str) -> bool:
        slot = self._slots.pop(deployment_id, None)
        if slot is None:
            return False
        # H12: best-effort cancel of in-flight tasks. The sync path cannot
        # await their completion; callers that need guaranteed cancellation
        # should use stop_deployment_async. Cancelled tasks auto-discard via
        # the done callback, but we drop the set entry immediately so a
        # subsequent stop_deployment_async does not see stale tasks.
        tasks = self._slot_tasks.pop(deployment_id, None)
        if tasks:
            for t in tasks:
                if not t.done():
                    t.cancel()
        self._save_state()
        logger.info(
            "Stopped deployment %s (strategy %s)", deployment_id, slot.strategy_id
        )
        return True

    async def stop_deployment_async(
        self, deployment_id: str, *, timeout: float = 5.0
    ) -> bool:
        """Stop a deployment safely (H12).

        Order of operations:
          1. Cancel all in-flight slot tasks for this deployment.
          2. Await their completion (with timeout) so no _execute_signal /
             _broadcast_status runs after the slot is removed.
          3. Pop the slot and persist state.

        asyncio.shield is used on the gather so a cancellation of this
        coroutine does not propagate into the slot tasks mid-cleanup.
        """
        tasks = list(self._slot_tasks.pop(deployment_id, ()))
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.gather(*tasks, return_exceptions=True)),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "stop_deployment_async: %d task(s) for %s did not "
                    "finish within %.1fs",
                    len(tasks),
                    deployment_id,
                    timeout,
                )
        slot = self._slots.pop(deployment_id, None)
        if slot is None:
            return False
        self._save_state()
        logger.info(
            "Stopped deployment %s (strategy %s) [async, %d task(s) cancelled]",
            deployment_id,
            slot.strategy_id,
            len(tasks),
        )
        return True

    def stop_by_strategy_id(self, strategy_id: str) -> list[str]:
        """Stop ALL deployments for a given strategy_id and return their ids."""
        to_remove = [
            dep_id
            for dep_id, slot in self._slots.items()
            if slot.strategy_id == strategy_id
        ]
        for dep_id in to_remove:
            # stop_deployment cancels in-flight slot tasks (H12) and persists.
            self.stop_deployment(dep_id)
        return to_remove

    async def stop_by_strategy_id_async(
        self, strategy_id: str, *, timeout: float = 5.0
    ) -> list[str]:
        """Async variant of stop_by_strategy_id (H12).

        Cancels + awaits in-flight slot tasks for each matching deployment.
        """
        to_remove = [
            dep_id
            for dep_id, slot in self._slots.items()
            if slot.strategy_id == strategy_id
        ]
        for dep_id in to_remove:
            await self.stop_deployment_async(dep_id, timeout=timeout)
        return to_remove

    # ------------------------------------------------------------------
    # Kline event handler
    # ------------------------------------------------------------------

    def _ensure_kline_hook(self) -> None:
        if self._kline_registered:
            return
        try:
            from selene_space_market import MarketDataService as _MDS

            svc: _MDS = self._market
            svc.on_kline(self._on_kline)
            self._kline_registered = True
        except Exception:
            logger.warning(
                "Failed to register kline handler for StrategyRunner", exc_info=True
            )

    def _on_kline(self, event: Any) -> None:
        """Synchronous kline callback — route to matching slots."""
        if not self._running:
            return
        kline_close_ts = _kline_label_ms(event)
        for slot in list(self._slots.values()):
            if not self._matches(slot, event):
                continue
            try:
                signal = slot.runtime.on_kline(event)
                if signal is not None:
                    slot.signals_emitted += 1
                    slot.last_signal_ts = kline_close_ts
                    sig_record = {
                        "ts": kline_close_ts,
                        "side": signal.side.value
                        if hasattr(signal.side, "value")
                        else str(signal.side),
                        "reason": getattr(signal, "reason", ""),
                        "strength": getattr(signal, "strength", None),
                        "price": event.close,
                        "symbol": slot.symbol,
                    }
                    slot.signal_history.append(sig_record)
                    if len(slot.signal_history) > MAX_SIGNAL_HISTORY:
                        slot.signal_history = slot.signal_history[-MAX_SIGNAL_HISTORY:]

                    # Invoke signal callbacks for WebSocket broadcast
                    for callback in self._signal_callbacks:
                        try:
                            callback(slot.deployment_id, signal)
                        except Exception:
                            logger.warning(
                                "Signal callback failed for deployment %s",
                                slot.deployment_id,
                                exc_info=True,
                            )

                    self._schedule_slot_task(
                        slot.deployment_id,
                        self._execute_signal(slot, signal, event.close, kline_close_ts),
                    )
                # Drive the trailing-stop tracker on every kline close.
                # Emits a SELL signal through the live pipeline when the
                # trailing stop is hit (no-op when no live_pipeline / no
                # tracker configured or no open position).
                if self._live_pipeline is not None and abs(slot.net_position) > 1e-12:
                    try:
                        ts_result = self._live_pipeline.check_trailing_stop(
                            slot.symbol, event.close, abs(slot.net_position)
                        )
                        if ts_result is not None:
                            logger.info(
                                "Trailing-stop result for %s: %s (%s)",
                                slot.symbol,
                                ts_result.action.value,
                                ts_result.reason,
                            )
                            # If the SELL executed, the runner's net_position
                            # was already updated inside _execute_signal via
                            # notify_fill. If it was blocked/skipped, the
                            # tracker state stays and will retry on next tick
                            # (subject to its internal emit-throttle).
                        # v017-frontend: drive the four-tier trailing tracker.
                        # check_tiered_trailing_stop calls on_tick →
                        # build_sell_signal → process_signal. Runs in parallel
                        # with the legacy two-tier tracker above (independent
                        # state, no mutual exclusion). hasattr fallback: old
                        # pipelines without this method are silently skipped.
                        if hasattr(self._live_pipeline, "check_tiered_trailing_stop"):
                            tiered_result = (
                                self._live_pipeline.check_tiered_trailing_stop(
                                    slot.symbol,
                                    event.close,
                                    abs(slot.net_position),
                                    liquidity=self._liquidity_provider.get(
                                        slot.symbol, event.close
                                    ),
                                )
                            )
                            if tiered_result is not None:
                                logger.info(
                                    "Tiered-trailing-stop result for %s: %s (%s)",
                                    slot.symbol,
                                    tiered_result.action.value,
                                    tiered_result.reason,
                                )
                    except Exception:
                        logger.debug(
                            "check_trailing_stop failed for %s",
                            slot.symbol,
                            exc_info=True,
                        )
                self._schedule_slot_task(
                    slot.deployment_id,
                    self._broadcast_status(slot),
                )
            except Exception:
                slot.errors += 1
                logger.warning(
                    "Error processing kline for deployment %s",
                    slot.deployment_id,
                    exc_info=True,
                )

    @staticmethod
    def _matches(slot: RunnerSlot, event: Any) -> bool:
        if event.symbol != slot.symbol:
            return False
        event_interval = getattr(event, "interval", None)
        # Accept base interval and aux interval (for TF_ features)
        aux_interval = slot.config_snapshot.get("aux_interval")
        if (
            event_interval
            and event_interval != slot.interval
            and event_interval != aux_interval
        ):
            return False
        # Only accept klines from a single source (per-slot exchange) to avoid
        # duplicate bars from multiple exchanges feeding the same runtime.
        event_exchange = getattr(event, "exchange", None)
        event_market_type = getattr(event, "market_type", None)
        snap = slot.config_snapshot
        expected_exchange = snap.get("exchange") or "binance"
        expected_market_type = snap.get("market_type") or "spot"
        if event_exchange and event_exchange != expected_exchange:
            return False
        if event_market_type and event_market_type != expected_market_type:
            return False
        return True

    async def _execute_signal(
        self,
        slot: RunnerSlot,
        signal: Any,
        current_price: float,
        signal_ts_ms: int | None = None,
    ) -> None:
        """Convert a Signal into a paper order."""
        try:
            from selene_space_types.trading import OrderSide

            # Serenity regime filter
            if (
                self._serenity_filter is not None
                and self._serenity_filter_mode != "off"
            ):
                serenity_dir = self._serenity_filter.get_direction(slot.symbol)
                if serenity_dir != 0:
                    opposes = (serenity_dir == -1 and signal.side == OrderSide.BUY) or (
                        serenity_dir == 1 and signal.side == OrderSide.SELL
                    )
                    if opposes:
                        if self._serenity_filter_mode == "hard":
                            logger.info(
                                "Serenity filter BLOCKED %s %s (score direction=%d)",
                                signal.side.value,
                                slot.symbol,
                                serenity_dir,
                            )
                            return
                        # soft: reduce strength to 30%
                        signal = signal.model_copy(
                            update={"strength": getattr(signal, "strength", 1.0) * 0.3}
                        )

            side = signal.side
            order_type = "market"
            # H19: internal notional/price math uses Decimal to avoid float drift.
            notional_usdt = Decimal(
                str(self._order_size)
            )  # USDT notional; convert to base qty below

            # EventGuard: scale position by event multiplier.
            # tg-spike-v1 applies its own macro overlay (±24h CPI/FOMC/NFP, strength × 0.5)
            # at signal-generation time. To avoid double-cutting the macro multiplier,
            # tg-spike-v1 only consults the TG realtime source (±2h SECURITY/REGULATORY
            # event protection), skipping macro and VIX sources.
            if self._event_guard is not None:
                if signal.strategy_id == "tg-spike-v1":
                    event_mult = self._event_guard.get_tg_multiplier(slot.symbol)
                else:
                    event_mult = self._event_guard.get_position_multiplier(slot.symbol)
                if event_mult < 0.01:
                    logger.info(
                        "EventGuard BLOCKED %s %s (event active)",
                        signal.side.value,
                        slot.symbol,
                    )
                    return
                notional_usdt = notional_usdt * Decimal(str(event_mult))

            # Convert USDT notional → base asset quantity at the current market price.
            # Binance `quantity` is base asset amount (e.g. BTC qty), not USDT notional.
            current_price_dec = Decimal(str(current_price))
            if current_price_dec > 0:
                quantity_dec = notional_usdt / current_price_dec
            else:
                quantity_dec = Decimal("0")
            quantity = float(quantity_dec)

            pos_side = "long" if side == OrderSide.BUY else "short"

            if slot.runtime.position == "flat" and side in (
                OrderSide.BUY,
                OrderSide.SELL,
            ):
                reduce_only = False
            else:
                reduce_only = True

            # Compute stop_loss for LIMITED_AUTO guard + journal/risk audit.
            # bb_v3 manages actual exits on subsequent bars; this stop_price is for
            # the pre-trade guard check and audit trail, NOT a Binance stop order.
            risk_cfg = (
                slot.config_snapshot.get("risk_params", {})
                if slot.config_snapshot
                else {}
            )
            stop_loss_pct = (
                float(risk_cfg.get("stop_loss_pct", 0.01)) if risk_cfg else 0.01
            )
            # H19: stop_price computed in Decimal then exported as float for the engine.
            if side == OrderSide.BUY:
                stop_price = float(
                    current_price_dec * (Decimal("1") - Decimal(str(stop_loss_pct)))
                )
            else:
                stop_price = float(
                    current_price_dec * (Decimal("1") + Decimal(str(stop_loss_pct)))
                )
            has_stop_loss = True

            # Route by execution mode: PAPER → PaperEngine, SHADOW → ShadowEngine,
            # CONFIRM/LIMITED_AUTO/FULL_AUTO → LiveEngine.
            mode = self._resolve_mode()
            engine = self._select_engine(mode)
            if engine is None:
                logger.warning(
                    "No execution engine available for mode=%s (deployment=%s); skipping order",
                    mode,
                    slot.deployment_id,
                )
                slot.errors += 1
                return

            # Run through LiveDecisionPipeline for non-paper modes (when wired).
            # The pipeline applies dedup/throttle/risk-precheck/limits/audit.
            # BLOCKED → skip the order; SKIPPED → skip silently; EXECUTED → continue.
            if self._live_pipeline is not None and mode != "paper":
                pipeline_result = self._live_pipeline.process_signal(signal)
                if pipeline_result.action.value != "executed":
                    logger.info(
                        "Live pipeline %s signal for deployment %s: %s",
                        pipeline_result.action.value,
                        slot.deployment_id,
                        pipeline_result.reason,
                    )
                    slot.errors += 1 if pipeline_result.action.value == "blocked" else 0
                    return

            # ExecutionModeGuard: hard limits for LIMITED_AUTO / FULL_AUTO.
            # PAPER/SHADOW/CONFIRM bypass here (CONFIRM is handled by the preview
            # flow at the API layer; PAPER/SHADOW have no live risk gate).
            if mode in ("limited_auto", "full_auto"):
                from app.core.dependencies import get_mode_guard

                guard = get_mode_guard()
                if guard is not None:
                    try:
                        guard.check(
                            symbol=slot.symbol,
                            side=side.value if hasattr(side, "value") else str(side),
                            order_type=order_type,
                            quantity=float(notional_usdt),  # guard uses USDT notional
                            leverage=1,
                            has_stop_loss=has_stop_loss,
                        )
                        guard.record_order(
                            symbol=slot.symbol, quantity=float(notional_usdt)
                        )
                    except Exception as exc:
                        logger.warning(
                            "Mode guard BLOCKED %s %s for deployment %s: %s",
                            side,
                            slot.symbol,
                            slot.deployment_id,
                            exc,
                        )
                        slot.errors += 1
                        return

            # Funding-arb dual-leg routing: when a dual-leg executor is wired
            # (live deployment with spot + perp clients) and the signal is a
            # funding-arb signal in a live mode, place BOTH legs through the
            # executor instead of a single spot order. Paper/shadow and other
            # strategies keep the single-order path below.
            if (
                self._dual_leg_executor is not None
                and getattr(signal, "strategy_id", None) == "funding_arb"
                and mode in ("confirm", "limited_auto", "full_auto")
            ):
                await self._execute_funding_arb_pair(
                    slot, signal, current_price, notional_usdt, mode
                )
                return

            supports_mark = getattr(engine, "supports_mark_price", False)
            extra_kwargs: dict[str, Any] = {}
            if supports_mark:
                extra_kwargs["mark_price"] = current_price
                extra_kwargs["signal_timestamp_ms"] = signal_ts_ms

            order = await engine.place_order(
                account_id=slot.account_id,
                symbol=slot.symbol,
                side=side.value if hasattr(side, "value") else str(side),
                order_type=order_type,
                quantity=quantity,
                leverage=1,
                margin_mode="cross",
                pos_side=pos_side,
                reduce_only=reduce_only,
                stop_price=stop_price,
                client_order_id=f"{slot.deployment_id}-{signal_ts_ms if signal_ts_ms is not None else int(time.time() * 1000)}",
                **extra_kwargs,
            )
            slot.orders_placed += 1
            # Record this slot as the most-recent orderer for this symbol so
            # the async fill-event callback (_on_fill_event) can route the
            # fill back to the originating deployment. Used as a fallback
            # when the Fill model does not carry client_order_id; the
            # primary routing key is fill.client_order_id itself.
            self._last_order_slot[slot.symbol] = slot.deployment_id
            # Paper-path FIFO fallback: queue deployment_id per-symbol so
            # concurrent orders on the same symbol do not silently lose fills
            # when _last_order_slot is overwritten by a later order. Live
            # path routes via fill.client_order_id (parsed in _on_fill_event).
            q = self._last_order_slot_queue.setdefault(slot.symbol, deque())
            q.append(slot.deployment_id)
            # Update local net-position tracker and feed the LiveDecisionPipeline
            # trailing-stop tracker (no-op when no live_pipeline / no tracker).
            signed_qty = quantity if side == OrderSide.BUY else -quantity
            # Store optimistic qty keyed by (symbol, deployment_id) so the
            # fill callback can OVERWRITE (not add) net_position with the
            # authoritative fill qty. Optimistic + fill-cancel-optimistic +
            # apply-real = single-counted (was doubled before fix).
            key = (slot.symbol, slot.deployment_id)
            slot._last_optimistic_qty[key] = signed_qty
            slot.net_position += signed_qty
            if self._live_pipeline is not None:
                try:
                    self._live_pipeline.notify_fill(
                        slot.symbol, current_price, side.value
                    )
                    # v017-frontend: feed tiered tracker (synchronous fill path)
                    if hasattr(self._live_pipeline, "notify_fill_tiered"):
                        self._live_pipeline.notify_fill_tiered(
                            slot.symbol, current_price, abs(signed_qty), side.value
                        )
                except Exception:
                    logger.debug(
                        "notify_fill failed for %s", slot.symbol, exc_info=True
                    )
            logger.info(
                "Runner placed order for deployment %s: %s %s %s @ %.2f (mode=%s, reason: %s)",
                slot.deployment_id,
                side,
                slot.symbol,
                order_type,
                current_price,
                mode,
                signal.reason,
            )

            await self._broadcast_signal(slot, signal, order)

        except Exception:
            slot.errors += 1
            logger.warning(
                "Failed to place order for deployment %s",
                slot.deployment_id,
                exc_info=True,
            )

    async def _execute_funding_arb_pair(
        self,
        slot: RunnerSlot,
        signal: Any,
        current_price: float,
        notional_usdt: Any,
        mode: str,
    ) -> None:
        """Place both legs of a funding-arb pair via the dual-leg executor.

        Replaces the single spot order for funding_arb in live modes. The
        executor opens spot BUY + perp SELL (or closes both), reconciles fills,
        and recovers a missing leg. Spot-side accounting is driven by
        ``PairResult.spot_position_delta`` so a flattened pair nets to zero.
        """
        from selene_space_types.trading import OrderSide

        cfg = slot.config_snapshot or {}
        # The perp leg's symbol must match what the deployed perp client's
        # adapter expects (Binance futures: "BTC/USDT"; OKX: "BTC/USDT-SWAP").
        # Default follows the funding_arb strategy convention.
        perp_symbol = str(cfg.get("perp_symbol") or f"{slot.symbol}-SWAP")

        executor = self._dual_leg_executor
        if executor is None:
            logger.error(
                "Dual-leg executor not configured for funding-arb pair (%s)",
                slot.deployment_id,
            )
            return
        if signal.side == OrderSide.BUY:
            result = await executor.open_delta_neutral(
                account_id=slot.account_id,
                strategy_id=signal.strategy_id,
                spot_symbol=slot.symbol,
                perp_symbol=perp_symbol,
                notional_usdt=float(notional_usdt),
                spot_price=current_price,
                perp_price=current_price,
                client_order_id_prefix=slot.deployment_id,
            )
        else:
            # Close qty = the spot position the runner is tracking; the perp
            # leg is the same base qty (a delta-neutral pair is 1:1).
            close_qty = float(abs(slot.net_position))
            result = await executor.close_delta_neutral(
                account_id=slot.account_id,
                strategy_id=signal.strategy_id,
                spot_symbol=slot.symbol,
                perp_symbol=perp_symbol,
                spot_qty=close_qty,
                perp_qty=close_qty,
                spot_price=current_price,
                perp_price=current_price,
                client_order_id_prefix=slot.deployment_id,
            )

        delta = float(result.spot_position_delta)
        side = result.leg("spot").side
        # Keep the same accounting as the single-order path: optimistic qty,
        # FIFO fill-routing, net-position, pipeline notify.
        slot.orders_placed += 1
        self._last_order_slot[slot.symbol] = slot.deployment_id
        q = self._last_order_slot_queue.setdefault(slot.symbol, deque())
        q.append(slot.deployment_id)
        key = (slot.symbol, slot.deployment_id)
        slot._last_optimistic_qty[key] = delta
        slot.net_position += delta
        if self._live_pipeline is not None:
            try:
                self._live_pipeline.notify_fill(slot.symbol, current_price, side.value)
                if hasattr(self._live_pipeline, "notify_fill_tiered"):
                    self._live_pipeline.notify_fill_tiered(
                        slot.symbol, current_price, abs(delta), side.value
                    )
            except Exception:
                logger.debug("notify_fill failed for %s", slot.symbol, exc_info=True)
        logger.info(
            "Runner placed funding-arb pair for deployment %s: spot %s via "
            "dual-leg executor (mode=%s, reason=%s, flattened=%s)",
            slot.deployment_id,
            side,
            mode,
            signal.reason,
            result.flattened,
        )
        await self._broadcast_signal(slot, signal, result)

    async def _broadcast_signal(
        self, slot: RunnerSlot, signal: Any, order: Any
    ) -> None:
        """Push strategy signal event via paper WS channel."""
        try:
            from app.api.v1.ws import broadcast_paper_event

            await broadcast_paper_event(
                slot.account_id,
                "strategy_signal",
                {
                    "deployment_id": slot.deployment_id,
                    "strategy_id": slot.strategy_id,
                    "signal": signal.model_dump()
                    if hasattr(signal, "model_dump")
                    else str(signal),
                    "order_id": order.id if hasattr(order, "id") else str(order),
                },
            )
        except Exception:
            logger.debug("Failed to broadcast strategy signal event", exc_info=True)

    async def _broadcast_status(self, slot: RunnerSlot) -> None:
        """Push runner_status event with live slot metrics via paper WS."""
        try:
            from app.api.v1.ws import broadcast_paper_event

            await broadcast_paper_event(
                slot.account_id,
                "runner_status",
                {
                    "deployment_id": slot.deployment_id,
                    "strategy_id": slot.strategy_id,
                    "symbol": slot.symbol,
                    "interval": slot.interval,
                    "position": slot.runtime.position,
                    "bar_count": slot.runtime.bar_count,
                    "signals_emitted": slot.signals_emitted,
                    "orders_placed": slot.orders_placed,
                    "errors": slot.errors,
                    "last_signal_ts": slot.last_signal_ts,
                },
            )
        except Exception:
            logger.debug("Failed to broadcast runner status", exc_info=True)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            state_file = self._state_dir / "deployments.json"
            data = {}
            for dep_id, slot in self._slots.items():
                data[dep_id] = {
                    "deployment_id": slot.deployment_id,
                    "strategy_id": slot.strategy_id,
                    "account_id": slot.account_id,
                    "config_snapshot": slot.config_snapshot,
                    "signals_emitted": slot.signals_emitted,
                    "orders_placed": slot.orders_placed,
                }
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save runner state", exc_info=True)

    async def _restore_state(self) -> None:
        state_file = self._state_dir / "deployments.json"
        if not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8-sig"))
            if not data:
                return

            seen_strategies: set[str] = set()
            restored = 0
            for dep_id, info in data.items():
                if dep_id in self._slots:
                    continue
                strategy_id = info.get("strategy_id", "")

                if strategy_id in seen_strategies:
                    logger.info(
                        "Skipping duplicate deployment %s for strategy %s",
                        dep_id,
                        strategy_id,
                    )
                    continue

                try:
                    config = await self._load_fresh_config(
                        strategy_id, info.get("config_snapshot", {})
                    )

                    if not _restore_config_deployable(config):
                        logger.warning(
                            "Skipping restore of deployment %s: strategy %s has empty rules",
                            dep_id,
                            strategy_id,
                        )
                        continue

                    err = await self.deploy(
                        deployment_id=dep_id,
                        strategy_config=config,
                        account_id=info.get("account_id", "auto"),
                    )
                except Exception as exc:
                    err = f"restore exception: {exc}"
                    logger.warning("Restore raised for deployment %s: %s", dep_id, exc)

                if err:
                    self._handle_restore_failure(dep_id, info, err)
                else:
                    restored += 1
                    seen_strategies.add(strategy_id)

            self._pending_restores = {
                dep_id: entry
                for dep_id, entry in self._pending_restores.items()
                if dep_id not in self._slots
            }
            if self._pending_restores:
                logger.warning(
                    "Restored %d deployments, %d pending retry: %s",
                    restored,
                    len(self._pending_restores),
                    ", ".join(self._pending_restores),
                )
            else:
                logger.info("Restored %d runner deployments from disk", restored)
        except Exception:
            logger.warning("Failed to restore runner state", exc_info=True)

    def _handle_restore_failure(
        self, dep_id: str, info: dict[str, Any], err: str
    ) -> None:
        classification = _classify_restore_failure(err)
        if classification == "permanent":
            logger.error(
                "Dropping deployment %s: permanent restore failure: %s",
                dep_id,
                err,
            )
            self._restore_attempts.pop(dep_id, None)
            self._pending_restores.pop(dep_id, None)
            return
        attempts = self._restore_attempts.get(dep_id, 0) + 1
        if attempts >= _RESTORE_MAX_ATTEMPTS:
            logger.error(
                "Dropping deployment %s after %d transient restore "
                "attempts, last error: %s",
                dep_id,
                attempts,
                err,
            )
            self._restore_attempts.pop(dep_id, None)
            self._pending_restores.pop(dep_id, None)
            return
        self._restore_attempts[dep_id] = attempts
        self._pending_restores[dep_id] = info
        logger.warning(
            "Restore of deployment %s deferred (transient, attempt %d/%d): %s",
            dep_id,
            attempts,
            _RESTORE_MAX_ATTEMPTS,
            err,
        )

    async def _restore_retry_loop(self) -> None:
        """Retry deferred restores so a startup hiccup doesn't lose deployments."""
        try:
            while self._pending_restores:
                await asyncio.sleep(_RESTORE_RETRY_INTERVAL)
                if not self._running:
                    return
                for dep_id, info in list(self._pending_restores.items()):
                    if dep_id in self._slots:
                        self._pending_restores.pop(dep_id, None)
                        self._restore_attempts.pop(dep_id, None)
                        continue
                    strategy_id = info.get("strategy_id", "")
                    try:
                        config = await self._load_fresh_config(
                            strategy_id, info.get("config_snapshot", {})
                        )
                        if not _restore_config_deployable(config):
                            logger.warning(
                                "Dropping pending restore %s: strategy %s has empty rules",
                                dep_id,
                                strategy_id,
                            )
                            self._handle_restore_failure(
                                dep_id, info, "Cannot deploy: rules empty"
                            )
                            continue
                        err = await self.deploy(
                            deployment_id=dep_id,
                            strategy_config=config,
                            account_id=info.get("account_id", "auto"),
                        )
                    except Exception as exc:
                        err = f"restore exception: {exc}"
                        logger.warning(
                            "Restore retry raised for deployment %s: %s",
                            dep_id,
                            exc,
                        )
                    if err:
                        self._handle_restore_failure(dep_id, info, err)
                    else:
                        logger.info("Restore retry succeeded for deployment %s", dep_id)
                        self._pending_restores.pop(dep_id, None)
                        self._restore_attempts.pop(dep_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Restore retry loop crashed", exc_info=True)

    async def _load_fresh_config(
        self,
        strategy_id: str,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        """Try to load the latest strategy config from the DB/store."""
        try:
            from app.api.v1.strategies import _get_strategy

            config = await _get_strategy(strategy_id)
            if config is not None:
                fresh = config.model_dump()
                logger.info(
                    "Restored fresh config for strategy %s (v%d, rules: entry=%s exit=%s)",
                    strategy_id,
                    fresh.get("version", 0),
                    bool(fresh.get("entry_rules")),
                    bool(fresh.get("exit_rules")),
                )
                return dict(fresh)
        except Exception:
            logger.debug(
                "Could not load fresh config for %s, using snapshot",
                strategy_id,
                exc_info=True,
            )
        return fallback
