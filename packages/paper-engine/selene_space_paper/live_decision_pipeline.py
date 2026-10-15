"""Limited-auto pipeline: hard caps layered on top of the base decision flow.

Every cap short-circuits in a fixed order — whitelist, single-order notional,
leverage, mandatory stop loss, daily count, daily notional, per-symbol
position, cooldown — so tests (and incident reviews) can rely on which
rejection wins when several would apply.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from selene_space_paper.decision_pipeline import (
    DecisionPipeline,
    PipelineAction,
    PipelineConfig,
    PipelineResult,
)
from selene_space_types.strategy import Signal


@dataclass
class LimitedAutoConfig:
    max_position_notional_per_symbol: float = 500.0
    max_daily_notional: float = 2000.0
    max_daily_orders: int = 20
    max_single_order_notional: float = 200.0
    allowed_symbols: list[str] | None = None
    max_leverage: int = 3
    require_stop_loss: bool = True
    min_stop_loss_pct: float = 0.01
    cooldown_between_same_symbol_s: float = 60


@dataclass
class LivePipelineConfig:
    base: PipelineConfig = field(default_factory=PipelineConfig)
    limits: LimitedAutoConfig = field(default_factory=LimitedAutoConfig)
    enforce_limits: bool = True


def _ms() -> int:
    return int(time.time() * 1000)


class LiveDecisionPipeline(DecisionPipeline):
    """Base pipeline plus hard notional / count / leverage guardrails."""

    def __init__(
        self,
        *,
        risk_engine: Any,
        order_manager: Any,
        audit_logger: Any | None = None,
        audit_ledger: Any | None = None,
        claim_auditor: Any | None = None,
        kill_switch: Any | None = None,
        config: PipelineConfig | None = None,
        live_config: LivePipelineConfig | None = None,
        risk_context_provider: Any | None = None,
        current_price_provider: Any | None = None,
        position_provider: Any | None = None,
        regime_provider: Any | None = None,
        trailing_stop: Any | None = None,
        tiered_trailing_stop: Any | None = None,
    ) -> None:
        super().__init__(
            risk_engine=risk_engine,
            order_manager=order_manager,
            audit_logger=audit_logger,
            audit_ledger=audit_ledger,
            claim_auditor=claim_auditor,
            kill_switch=kill_switch,
            config=config or (live_config.base if live_config else None),
            risk_context_provider=risk_context_provider,
            current_price_provider=current_price_provider,
            regime_provider=regime_provider,
        )
        self._live_cfg = live_config or LivePipelineConfig()
        self._position_provider = position_provider
        self._trailing_stop = trailing_stop
        self._tiered_trailing_stop = tiered_trailing_stop
        self._daily_orders = 0
        self._daily_notional = 0.0
        self._last_fill_epoch: dict[str, float] = {}
        # Executed notional per symbol this session — the position-cap floor
        # when no external position provider is wired in.
        self._symbol_notional: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Daily counters
    # ------------------------------------------------------------------

    def reset_daily_counters(self) -> None:
        self._daily_orders = 0
        self._daily_notional = 0.0

    def daily_status(self) -> dict[str, Any]:
        return {
            "daily_orders": self._daily_orders,
            "daily_notional": self._daily_notional,
        }

    # ------------------------------------------------------------------
    # Limit enforcement
    # ------------------------------------------------------------------

    def _enforce_limits(self, signal: Signal, intent: Any) -> PipelineResult | None:
        lim = self._live_cfg.limits

        if lim.allowed_symbols is not None and intent.symbol not in lim.allowed_symbols:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason=f"{intent.symbol} not in allowed list",
            )

        single = float(intent.quantity) * (float(intent.price) if intent.price else 1.0)
        if single > lim.max_single_order_notional:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason=(
                    f"Single order notional {single:.2f} exceeds cap "
                    f"{lim.max_single_order_notional:.2f}"
                ),
            )

        leverage = int(intent.risk_params.get("leverage", 1))
        if leverage > lim.max_leverage:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason=f"Leverage {leverage} exceeds maximum {lim.max_leverage}",
            )

        stop = intent.risk_params.get("stop_loss")
        if lim.require_stop_loss and stop is None:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason="Stop loss is required before any entry",
            )

        if self._daily_orders >= lim.max_daily_orders:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason=(
                    f"Daily order limit reached ({self._daily_orders}/"
                    f"{lim.max_daily_orders})"
                ),
            )

        if self._daily_notional + single > lim.max_daily_notional:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason=(
                    f"Daily notional {self._daily_notional + single:.2f} would "
                    f"exceed cap {lim.max_daily_notional:.2f}"
                ),
            )

        held = self._current_position_notional(intent.symbol)
        if held + single > lim.max_position_notional_per_symbol:
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason=(
                    f"Position limit for {intent.symbol}: {held + single:.2f} "
                    f"would exceed {lim.max_position_notional_per_symbol:.2f}"
                ),
            )

        last_touch = self._last_fill_epoch.get(intent.symbol)
        if (
            last_touch is not None
            and (time.time() - last_touch) < lim.cooldown_between_same_symbol_s
        ):
            return PipelineResult(
                action=PipelineAction.SKIPPED,
                reason=(
                    f"Throttled: {intent.symbol} still cooling down between orders"
                ),
            )

        return None

    def _current_position_notional(self, symbol: str) -> float:
        if self._position_provider is not None:
            try:
                held = self._position_provider(symbol)
                if held is not None:
                    return float(held)
            except Exception:
                pass
        return self._symbol_notional.get(symbol, 0.0)

    # ------------------------------------------------------------------
    # Pipeline override
    # ------------------------------------------------------------------

    def process_signal(self, signal: Signal) -> PipelineResult:
        # Stage 1: kill switch has absolute priority over everything else.
        if self._kill_switch is not None and bool(
            getattr(self._kill_switch, "is_active", False)
        ):
            return PipelineResult(
                action=PipelineAction.BLOCKED,
                reason="Kill switch engaged — new orders are frozen",
            )

        # Stage 2: build the trade intent from the raw signal.
        intent = self._signal_to_intent(signal)

        # Stage 3: hard limit enforcement (whitelist/notional/leverage/...).
        if self._live_cfg.enforce_limits:
            verdict = self._enforce_limits(signal, intent)
            if verdict is not None:
                return self._audit_event(signal, intent, verdict)

        # Stage 4: run the base pipeline (dedupe/throttle/validation/risk/place).
        placed = super().process_signal(signal)
        # Stage 5: accumulate daily counters on successful execution.
        if placed.action == PipelineAction.EXECUTED:
            qty = float(intent.quantity)
            px = float(intent.price) if intent.price else 1.0
            notional = qty * px
            self._daily_orders += 1
            self._daily_notional += notional
            # Stage 6: per-symbol notional tracking for the position cap.
            self._symbol_notional[intent.symbol] = (
                self._symbol_notional.get(intent.symbol, 0.0) + notional
            )
            self._last_fill_epoch[signal.symbol] = time.time()
        # Stage 7: return the base pipeline's verdict unchanged.
        return placed

    # ------------------------------------------------------------------
    # Trailing-stop bridge
    # ------------------------------------------------------------------

    def notify_fill(self, symbol: str, fill_price: float, side: str) -> None:
        """Feed a fill into the simple trailing tracker; SELL closes its state."""
        if self._trailing_stop is None:
            return
        try:
            if side.lower() == "buy":
                self._trailing_stop.open_position(symbol, fill_price)
            else:
                self._trailing_stop.clear(symbol)
        except Exception:
            pass

    def check_trailing_stop(
        self, symbol: str, current_price: float, position_quantity: float
    ) -> PipelineResult | None:
        """Tick the tracker; on an emitted SELL run it back through this pipeline."""
        if self._trailing_stop is None:
            return None
        try:
            sell = self._trailing_stop.update(symbol, current_price, position_quantity)
        except Exception:
            return None
        if sell is None:
            return None
        return self.process_signal(sell)

    def notify_fill_tiered(
        self, symbol: str, fill_price: float, fill_qty: float, side: str
    ) -> None:
        if self._tiered_trailing_stop is None:
            return
        try:
            if side.lower() == "buy":
                self._tiered_trailing_stop.on_fill(symbol, fill_qty, fill_price)
            else:
                self._tiered_trailing_stop.clear(symbol)
        except Exception:
            pass

    def check_tiered_trailing_stop(
        self,
        symbol: str,
        current_price: float,
        position_quantity: float,
        *,
        liquidity: Any | None = None,
    ) -> PipelineResult | None:
        if self._tiered_trailing_stop is None:
            return None
        try:
            state = self._tiered_trailing_stop.on_tick(
                symbol, current_price, position_quantity, liquidity=liquidity
            )
            if state is None or not state.closed:
                return None
            sell = self._tiered_trailing_stop.build_sell_signal(
                state, position_quantity=position_quantity
            )
        except Exception:
            return None
        if sell is None:
            return None
        return self.process_signal(sell)


def signal_quantity(signal: Signal) -> Any:
    """Signal may carry a quantity attribute depending on shared-types vintage."""
    return getattr(signal, "quantity", 0.01)
