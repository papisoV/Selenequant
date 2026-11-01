"""Continuous risk monitor for live trading.

Periodically evaluates portfolio-level risk metrics (not just pre-trade)
and triggers kill switch on critical conditions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from selene_space_risk.position_audit import (
    PositionTargetProvider,
    audit_positions_against_targets,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskSnapshot:
    """Point-in-time risk state for the portfolio."""

    total_equity: float = 0.0
    peak_equity: float = 0.0
    drawdown_pct: float = 0.0
    margin_used_pct: float = 0.0
    position_count: int = 0
    max_single_position_pct: float = 0.0
    closest_liquidation_pct: float = 100.0
    timestamp: int = 0


class ContinuousRiskMonitor:
    """Periodically evaluates portfolio-level risk.

    Runs on a configurable interval (default 30s) and checks:
    - Drawdown vs peak equity
    - Position concentration
    - Margin utilization
    - Liquidation distance
    - Triggers kill switch on CRITICAL conditions

    Usage::

        monitor = ContinuousRiskMonitor(
            risk_engine=risk_engine,
            kill_switch=kill_switch,
        )
        await monitor.start()
        # ... later ...
        await monitor.stop()
    """

    def __init__(
        self,
        *,
        risk_engine: object | None = None,
        kill_switch: object | None = None,
        execution_engine: object | None = None,
        interval_s: float = 30.0,
        full_auto_interval_s: float = 15.0,
        drawdown_critical_pct: float = 15.0,
        drawdown_warning_pct: float = 8.0,
        margin_critical_pct: float = 90.0,
        liquidation_warning_pct: float = 5.0,
        max_position_pct: float = 50.0,
        flatten_hook: object | None = None,
        flatten_on_liquidation: bool = True,
        fills_ledger: object | None = None,
        daily_loss_flatten_pct: float = 0.01,
        account_id: str = "live",
        target_provider: PositionTargetProvider | None = None,
    ) -> None:
        self._risk_engine = risk_engine
        self._kill_switch = kill_switch
        self._execution_engine = execution_engine
        self._interval_s = interval_s
        self._full_auto_interval_s = full_auto_interval_s
        self._drawdown_critical_pct = drawdown_critical_pct
        self._drawdown_warning_pct = drawdown_warning_pct
        self._margin_critical_pct = margin_critical_pct
        self._liquidation_warning_pct = liquidation_warning_pct
        self._max_position_pct = max_position_pct
        self._flatten_hook = flatten_hook
        self._flatten_on_liquidation = flatten_on_liquidation
        self._fills_ledger = fills_ledger
        self._daily_loss_flatten_pct = daily_loss_flatten_pct
        self._account_id = account_id
        self._target_provider = target_provider

        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._peak_equity: float = 0.0
        self._last_snapshot: RiskSnapshot | None = None
        self._last_positions: list = []

    @property
    def last_snapshot(self) -> RiskSnapshot | None:
        return self._last_snapshot

    async def start(self) -> None:
        """Start the periodic risk monitoring loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="continuous-risk-monitor")
        logger.info("Continuous risk monitor started (interval=%.0fs)", self._interval_s)

    async def stop(self) -> None:
        """Stop the periodic risk monitoring loop."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Continuous risk monitor stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                interval = self._get_effective_interval()
                await asyncio.sleep(interval)
                if not self._running:
                    break
                await self._check_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in continuous risk monitor")

    def _get_effective_interval(self) -> float:
        """Use faster interval in FULL_AUTO mode."""
        try:
            from selene_space_types.trading import ExecutionMode
            from app.core.dependencies import get_execution_mode
            mode_str = get_execution_mode()
            if mode_str == ExecutionMode.FULL_AUTO.value:
                return self._full_auto_interval_s
        except ImportError:
            pass
        return self._interval_s

    async def _check_cycle(self) -> None:
        """Run a single risk check cycle."""
        snapshot = self._compute_snapshot()
        if snapshot is None:
            return

        self._last_snapshot = snapshot

        # Update peak equity
        if snapshot.total_equity > self._peak_equity:
            self._peak_equity = snapshot.total_equity

        # Calculate drawdown — skip when total_equity is 0 (WS gap or empty
        # balances) to avoid false-positive 100% drawdown from missing data.
        # Bug F: paper/live engine balances not synced yet → total_equity=0
        # → (1 - 0/peak) * 100 = 100% false drawdown → KillSwitch false trip.
        if self._peak_equity > 0 and snapshot.total_equity > 0:
            snapshot.drawdown_pct = (1.0 - snapshot.total_equity / self._peak_equity) * 100.0

        # Check critical conditions
        critical_reasons: list[str] = []
        warnings: list[str] = []

        # Drawdown check
        if snapshot.drawdown_pct >= self._drawdown_critical_pct:
            critical_reasons.append(
                f"Drawdown {snapshot.drawdown_pct:.1f}% >= {self._drawdown_critical_pct:.0f}%"
            )
        elif snapshot.drawdown_pct >= self._drawdown_warning_pct:
            warnings.append(
                f"Drawdown {snapshot.drawdown_pct:.1f}% >= {self._drawdown_warning_pct:.0f}%"
            )

        # Margin utilization check
        if snapshot.margin_used_pct >= self._margin_critical_pct:
            critical_reasons.append(
                f"Margin usage {snapshot.margin_used_pct:.1f}% >= {self._margin_critical_pct:.0f}%"
            )

        # Position concentration
        if snapshot.max_single_position_pct >= self._max_position_pct:
            warnings.append(
                f"Single position {snapshot.max_single_position_pct:.1f}% >= {self._max_position_pct:.0f}%"
            )

        # Liquidation proximity
        if snapshot.closest_liquidation_pct <= self._liquidation_warning_pct:
            warnings.append(
                f"Position within {snapshot.closest_liquidation_pct:.1f}% of liquidation"
            )
            # #8 (exception): proximity is a warning, not a kill — flatten only,
            # opt-out via flatten_on_liquidation (default True).
            if self._flatten_on_liquidation and self._flatten_hook is not None:
                await self._flatten_hook.fire(reason="liquidation proximity")

        # Fire critical: activate kill switch
        if critical_reasons and self._kill_switch is not None:
            reason = " | ".join(critical_reasons)
            logger.critical("RISK CRITICAL: %s — activating kill switch", reason)
            try:
                self._kill_switch.auto_activate(reason, triggered_by_rule="continuous_risk_monitor")
            except Exception:
                logger.exception("Failed to activate kill switch from risk monitor")
            # Notify
            try:
                from selene_space_pulse.notify import send_trade_alert
                send_trade_alert(f"RISK CRITICAL: {reason}")
            except Exception:
                pass

        # Flatten on critical portfolio conditions (drawdown / margin). Independent
        # of kill_switch presence — a critical portfolio state warrants unwinding.
        if critical_reasons and self._flatten_hook is not None:
            await self._flatten_hook.fire(reason="critical: " + " | ".join(critical_reasons))

        # Daily loss fuse (#3): realized loss today >= equity * pct → kill + flatten.
        if (
            self._fills_ledger is not None
            and snapshot.total_equity > 0
            and self._daily_loss_flatten_pct > 0
        ):
            try:
                from datetime import datetime, timezone
                today_utc = datetime.now(timezone.utc).date()
                daily_pnl = float(
                    self._fills_ledger.get_daily_realized_pnl(self._account_id, today_utc)
                )
                if daily_pnl < 0 and abs(daily_pnl) >= snapshot.total_equity * self._daily_loss_flatten_pct:
                    fuse_reason = (
                        f"Daily loss {daily_pnl:.2f} >= "
                        f"{self._daily_loss_flatten_pct * 100:.0f}% of equity"
                    )
                    logger.critical("RISK CRITICAL: %s — flattening", fuse_reason)
                    if self._kill_switch is not None:
                        try:
                            self._kill_switch.auto_activate(
                                fuse_reason, triggered_by_rule="daily_loss_fuse"
                            )
                        except Exception:
                            logger.exception("Failed to activate kill switch from daily loss fuse")
                    if self._flatten_hook is not None:
                        await self._flatten_hook.fire(reason="daily loss fuse")
            except Exception:
                logger.exception("Daily loss fuse check failed")

        # Log warnings
        if warnings:
            logger.warning("RISK WARNING: %s", " | ".join(warnings))
            try:
                from selene_space_pulse.notify import send_trade_alert
                send_trade_alert(f"RISK WARNING: {' | '.join(warnings)}")
            except Exception:
                pass

        # Position audit vs strategy targets — advisory only. It never appends
        # to critical_reasons and never touches the kill switch; the banned
        # check here is a post-trade watch on held positions, distinct from
        # SymbolBlacklistRule (rules.py), which is a pre-trade CRITICAL hard-block.
        if self._target_provider is not None:
            self._run_position_audit()

    def _run_position_audit(self) -> None:
        """Emit advisory position-vs-target alerts (never feeds the kill switch)."""
        provider = self._target_provider
        if provider is None:
            return
        targets = provider.get_targets()
        banned = getattr(provider, "get_banned", lambda: frozenset())()
        alerts = audit_positions_against_targets(
            self._last_positions,
            targets,
            banned=frozenset(banned),
        )
        for alert in alerts:
            summary = f"{alert.rule_id}: {alert.message}"
            logger.warning("POSITION AUDIT [%s] %s", alert.level.value, summary)
            try:
                from selene_space_pulse.notify import send_trade_alert
                send_trade_alert(f"POSITION AUDIT: {summary}")
            except Exception:
                pass

    def _compute_snapshot(self) -> RiskSnapshot | None:
        """Compute current risk snapshot from engine state.

        Tries execution_engine first (live), then risk_engine context.
        Returns None if no data is available.

        H15: When the execution engine exposes a ``compute_equity`` method
        (paper engine does; live engine does not yet), prefer it over the
        raw ``free + locked`` sum because the latter ignores unrealized PnL
        on open positions — causing false-positive drawdown trips when
        positions are in profit and missed risk events when in loss.
        """
        now_ms = int(time.time() * 1000)

        # Primary: fetch directly from execution engine (live or paper)
        if self._execution_engine is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside the async loop — use run_until_complete won't work.
                    # Schedule the fetch and use cached data instead.
                    return self._compute_from_sync_state()
                else:
                    # H15: prefer compute_equity() when available so total_equity
                    # reflects unrealized PnL. Falls back to get_balances for
                    # engines that don't implement it (e.g. live_engine).
                    compute_equity = getattr(self._execution_engine, "compute_equity", None)
                    if callable(compute_equity):
                        try:
                            equity = float(
                                compute_equity(self._engine_default_account_id())
                            )
                            positions = loop.run_until_complete(
                                self._execution_engine.get_positions("default"),
                            )
                            return self._build_snapshot(
                                [], positions, now_ms, unrealized_pnl=equity,
                            )
                        except Exception:
                            logger.debug(
                                "compute_equity failed; falling back to balances",
                                exc_info=True,
                            )
                    balances = loop.run_until_complete(
                        self._execution_engine.get_balances("default"),
                    )
                    positions = loop.run_until_complete(
                        self._execution_engine.get_positions("default"),
                    )
                    return self._build_snapshot(balances, positions, now_ms)
            except Exception:
                logger.debug("Could not compute snapshot from execution engine", exc_info=True)

        # Fallback: risk engine context
        if self._risk_engine is not None and hasattr(self._risk_engine, "get_context"):
            try:
                ctx = self._risk_engine.get_context()
                return self._snapshot_from_context(ctx, now_ms)
            except Exception:
                logger.debug("Could not compute risk snapshot from engine context")

        return None

    def _engine_default_account_id(self) -> str:
        """Resolve the execution engine's default account id (best effort)."""
        engine = self._execution_engine
        for attr in ("_default_account_id", "default_account_id"):
            val = getattr(engine, attr, None)
            if isinstance(val, str) and val:
                return val
        return "default"

    def _compute_from_sync_state(self) -> RiskSnapshot | None:
        """Build snapshot from engine's in-memory state (sync access for async loop).

        H15: prefer ``compute_equity()`` (sync, paper engine) so total_equity
        includes unrealized PnL. Falls back to direct ``_balances`` /
        ``_positions`` reads for engines without that method.
        """
        now_ms = int(time.time() * 1000)
        engine = self._execution_engine

        compute_equity = getattr(engine, "compute_equity", None)
        if callable(compute_equity):
            try:
                equity = float(compute_equity(self._engine_default_account_id()))
                # Positions are still needed for concentration / liquidation checks.
                # Prefer a sync accessor when the engine exposes one (paper engine
                # uses ``_position_mgr.get_open_positions``); fall back to an
                # empty list — engines without ``_positions`` (live_engine) will
                # simply report zero concentration, which is safe.
                positions: list = []
                position_mgr = getattr(engine, "_position_mgr", None)
                if position_mgr is not None:
                    get_open = getattr(position_mgr, "get_open_positions", None)
                    if callable(get_open):
                        try:
                            positions = list(
                                get_open(self._engine_default_account_id())
                            )
                        except Exception:
                            positions = []
                else:
                    raw_positions = getattr(engine, "_positions", [])
                    if isinstance(raw_positions, list):
                        positions = raw_positions
                return self._build_snapshot(
                    [], positions, now_ms, unrealized_pnl=equity,
                )
            except Exception:
                logger.debug(
                    "compute_equity (sync) failed; falling back to raw state",
                    exc_info=True,
                )

        balances = getattr(engine, "_balances", [])
        positions = getattr(engine, "_positions", [])

        return self._build_snapshot(balances, positions, now_ms)

    def _build_snapshot(
        self,
        balances: list,
        positions: list,
        now_ms: int,
        *,
        unrealized_pnl: float | None = None,
    ) -> RiskSnapshot:
        """Build a RiskSnapshot from balances and positions lists.

        H15: ``unrealized_pnl`` (when provided by ``compute_equity``) is added
        to the wallet balance total so ``total_equity`` reflects the true
        account value including open-position PnL.
        """
        # Capture the current cycle's held positions for the position-vs-target
        # audit. Every Position-object path flows through this funnel, so the
        # audit always sees the current cycle's holdings (the context-dict path
        # below resets to empty instead).
        self._last_positions = list(positions)

        total_equity = 0.0
        margin_used = 0.0
        for b in balances:
            free = float(getattr(b, "free", 0.0) or 0.0)
            locked = float(getattr(b, "locked", 0.0) or 0.0)
            total_equity += free + locked

        if unrealized_pnl is not None:
            total_equity += float(unrealized_pnl)

        position_count = len(positions)
        max_position_pct = 0.0
        closest_liquidation_pct = 100.0

        for p in positions:
            qty = float(getattr(p, "quantity", 0.0) or 0.0)
            entry = float(getattr(p, "avg_entry_price", 0.0) or 0.0)
            notional = abs(qty * entry) if entry else 0.0

            if total_equity > 0:
                pct = (notional / total_equity) * 100
                if pct > max_position_pct:
                    max_position_pct = pct

            liq_dist_raw = getattr(p, "liquidation_distance_pct", 100.0)
            liq_dist = float(liq_dist_raw) if liq_dist_raw is not None else 100.0
            if isinstance(liq_dist, (int, float)) and liq_dist < closest_liquidation_pct:
                closest_liquidation_pct = liq_dist

            # Margin proxy: use position notional * maintenance margin rate (approx)
            margin_used += notional * 0.005  # ~0.5% maintenance margin estimate

        margin_used_pct = (margin_used / total_equity * 100) if total_equity > 0 else 0.0

        return RiskSnapshot(
            total_equity=total_equity,
            peak_equity=self._peak_equity,
            margin_used_pct=margin_used_pct,
            position_count=position_count,
            max_single_position_pct=max_position_pct,
            closest_liquidation_pct=closest_liquidation_pct,
            timestamp=now_ms,
        )

    def _snapshot_from_context(self, ctx: dict, now_ms: int) -> RiskSnapshot:
        """Build snapshot from risk engine context dict."""
        # Context positions are {symbol: {notional, ...}} dicts, not Position
        # objects, so there is no Position-typed data for the position audit
        # here — reset to empty rather than re-audit a prior cycle's stale
        # holdings (disclosed blind spot: audit is silent on this path).
        self._last_positions = []
        total_equity = float(ctx.get("total_equity", 0))
        positions = ctx.get("positions", {})

        position_count = len(positions)
        max_position_pct = 0.0
        closest_liquidation_pct = 100.0
        margin_used_pct = 0.0

        if total_equity > 0 and positions:
            for _sym, pos_data in positions.items():
                pos_notional = float(pos_data.get("notional", 0))
                pos_pct = (pos_notional / total_equity) * 100
                if pos_pct > max_position_pct:
                    max_position_pct = pos_pct

                liq_dist = float(pos_data.get("liquidation_distance_pct", 100))
                if liq_dist < closest_liquidation_pct:
                    closest_liquidation_pct = liq_dist

            margin_used = float(ctx.get("margin_used", 0))
            margin_used_pct = (margin_used / total_equity) * 100

        return RiskSnapshot(
            total_equity=total_equity,
            peak_equity=self._peak_equity,
            margin_used_pct=margin_used_pct,
            position_count=position_count,
            max_single_position_pct=max_position_pct,
            closest_liquidation_pct=closest_liquidation_pct,
            timestamp=now_ms,
        )
