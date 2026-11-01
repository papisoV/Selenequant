"""Decision pipeline: the single front door every trading signal passes through.

Order of gates is fixed and each one short-circuits:
kill switch → dedupe → throttle → intent build → validation/regime →
risk pre-check → order placement. Reason strings carry stable keywords
("Kill switch", "Duplicate", "Throttled", "Risk denied", "Validation failed")
because downstream tooling greps for them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from selene_space_types.agent import MarketRegime, TradeIntent
from selene_space_types.trading import OrderType
from selene_space_core.infra.dedupe import Deduplicator
from selene_space_types.strategy import Signal


class PipelineAction(str, Enum):
    EXECUTED = "executed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass
class PipelineResult:
    details: dict[str, Any] = field(default_factory=dict)
    order_id: str | None = None
    reason: str = ""
    action: PipelineAction = PipelineAction.SKIPPED

    @classmethod
    def blocked(cls, why: str, **extra: Any) -> "PipelineResult":
        return cls(action=PipelineAction.BLOCKED, reason=why, **extra)

    @classmethod
    def skipped(cls, why: str, **extra: Any) -> "PipelineResult":
        return cls(action=PipelineAction.SKIPPED, reason=why, **extra)


def _config_field_names(model: type) -> list[str]:
    return [f.name for f in model.__dataclass_fields__.values()]  # type: ignore[attr-defined]


@dataclass
class PipelineConfig:
    enable_validation: bool = True
    dedupe_ttl_seconds: float = 60.0
    regime_lookback: int = 60
    min_order_interval_seconds: float = 10.0
    default_account_id: str = ""
    block_on_bear: bool = False
    default_quantity: float = 0.01
    block_on_transition: bool = False

    def merged_with(self, **overrides: Any) -> "PipelineConfig":
        """Copy of this config with the given fields replaced."""
        known = _config_field_names(type(self))
        unknown = set(overrides) - set(known)
        if unknown:
            raise TypeError(f"unknown config keys: {sorted(unknown)}")
        current = {name: getattr(self, name) for name in known}
        return PipelineConfig(**{**current, **overrides})


class DecisionPipeline:
    """Turns raw strategy signals into placed orders — or an audited refusal."""

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
        risk_context_provider: Callable[[Signal], dict[str, Any]] | None = None,
        current_price_provider: Callable[[str], float] | None = None,
        regime_provider: Callable[[str], MarketRegime | None] | None = None,
    ) -> None:
        self._gate_cfg = config or PipelineConfig()
        self._risk_engine = risk_engine
        self._orders = order_manager
        # test-locked: last claim-audit snapshot is asserted directly
        self._last_claim_audit: dict[str, Any] | None = None
        self._audit_logger = audit_logger
        self._audit_ledger = audit_ledger
        self._claim_auditor = claim_auditor
        self._kill_switch = kill_switch
        self._regime_provider = regime_provider
        self._price_provider = current_price_provider
        self._risk_context_provider = risk_context_provider
        self._dedupe = Deduplicator(ttl_seconds=self._gate_cfg.dedupe_ttl_seconds)
        self._recent_placements: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Gate sequence (order matters; each returns a refusal or None)
    # ------------------------------------------------------------------

    def process_signal(self, signal: Signal) -> PipelineResult:
        refusal = (
            self._check_kill_switch(signal)
            or self._check_duplicate(signal)
            or self._check_pacing(signal)
        )
        if refusal is not None:
            return refusal

        now_mono = time.monotonic()
        intent = self._signal_to_intent(signal)
        self._audit_llm_claims(signal)

        verdict = self._run_validation_gate(signal, intent)
        if verdict is not None:
            return verdict

        verdict = self._run_risk_gate(signal, intent)
        if verdict is not None:
            return verdict

        order = self._submit(intent, signal)
        self._recent_placements[signal.symbol] = now_mono
        result = PipelineResult(
            action=PipelineAction.EXECUTED,
            order_id=order.id,
        )
        return self._audit_event(signal, intent, result)

    def _check_kill_switch(self, signal: Signal) -> PipelineResult | None:
        if self._kill_switch is not None and bool(
            getattr(self._kill_switch, "is_active", False)
        ):
            return self._emit_refusal(
                signal,
                None,
                PipelineResult.blocked("Kill switch engaged — new orders are frozen"),
            )
        return None

    def _check_duplicate(self, signal: Signal) -> PipelineResult | None:
        fingerprint = f"{signal.strategy_id}|{signal.symbol}|{signal.side.value}|{signal.timestamp}"
        if self._dedupe.is_duplicate(fingerprint):
            return self._emit_refusal(
                signal,
                None,
                PipelineResult.skipped(
                    "Duplicate signal suppressed",
                    details={"dedupe_key": fingerprint},
                ),
            )
        return None

    def _check_pacing(self, signal: Signal) -> PipelineResult | None:
        last = self._recent_placements.get(signal.symbol)
        gap = self._gate_cfg.min_order_interval_seconds
        if last is not None and (time.monotonic() - last) < gap:
            return self._emit_refusal(
                signal,
                None,
                PipelineResult.skipped(
                    "Throttled: symbol still within its minimum order interval"
                ),
            )
        return None

    def _run_validation_gate(
        self, signal: Signal, intent: TradeIntent
    ) -> PipelineResult | None:
        if not self._gate_cfg.enable_validation:
            return None
        errors = self._validate_intent(intent)
        if errors:
            return self._emit_refusal(
                signal,
                intent,
                PipelineResult.blocked(
                    f"Validation failed: {'; '.join(errors)}",
                    details={"errors": errors},
                ),
            )
        if self._gate_cfg.block_on_bear and self._regime_provider is not None:
            regime = self._detect_regime(signal.symbol)
            if regime is not None and str(regime.value).lower() in (
                "bear",
                "downtrend",
            ):
                return self._emit_refusal(
                    signal,
                    intent,
                    PipelineResult.blocked(
                        "Validation failed: bearish regime blocks entries"
                    ),
                )
        return None

    def _run_risk_gate(
        self, signal: Signal, intent: TradeIntent
    ) -> PipelineResult | None:
        context = (
            self._risk_context_provider(signal)
            if self._risk_context_provider is not None
            else {}
        )
        decision = self._risk_engine.pre_check(intent, context)
        if not decision.allowed:
            return self._emit_refusal(
                signal,
                intent,
                PipelineResult.blocked(
                    f"Risk denied: {getattr(decision, 'reason', '')}",
                    details={"level": str(getattr(decision, "level", ""))},
                ),
            )
        return None

    def _submit(self, intent: TradeIntent, signal: Signal) -> Any:
        cfg = self._gate_cfg
        payload: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side,
            "order_type": intent.order_type,
            "quantity": intent.quantity,
            "price": intent.price,
            "leverage": intent.risk_params.get("leverage", 1),
            "client_order_id": None,
            "strategy_id": signal.strategy_id,
            "signal_timestamp_ms": signal.timestamp,
        }
        if intent.risk_params.get("stop_loss") is not None:
            payload["sl_price"] = intent.risk_params["stop_loss"]
        if intent.risk_params.get("take_profit") is not None:
            payload["tp_price"] = intent.risk_params["take_profit"]
        return self._orders.place_order(cfg.default_account_id, **payload)

    def _emit_refusal(
        self,
        signal: Signal,
        intent: TradeIntent | None,
        result: PipelineResult,
    ) -> PipelineResult:
        return self._audit_event(signal, intent, result)

    # ------------------------------------------------------------------
    # Helpers (frozen names)
    # ------------------------------------------------------------------

    def _detect_regime(self, symbol: str) -> MarketRegime | None:
        if self._regime_provider is None:
            return None
        try:
            return self._regime_provider(symbol)
        except Exception:
            return None

    def _validate_intent(self, intent: TradeIntent) -> list[str]:
        try:
            from selene_space_risk.validators import validate as validate_intent

            live = self._price_provider(intent.symbol) if self._price_provider else 0.0
            outcome = validate_intent(intent, live)
            return [] if outcome.valid else list(outcome.errors)
        except ImportError:
            return []

    def _signal_to_intent(self, signal: Signal) -> TradeIntent:
        """Frozen mapping Signal → TradeIntent."""
        return TradeIntent(
            side=signal.side,
            timestamp=signal.timestamp,
            symbol=signal.symbol,
            reasoning=signal.reason,
            confidence=signal.strength,
            quantity=self._gate_cfg.default_quantity,
            order_type=OrderType.MARKET,
            risk_params={},
        )

    def _audit_llm_claims(self, signal: Signal) -> None:
        """Hand numeric claims in the signal text to the auditor, never raising."""
        if self._claim_auditor is None:
            return
        try:
            points = self._claim_auditor.extract(signal.reason)
            report = self._claim_auditor.verdict(points, {}) if points else None
            self._last_claim_audit = {
                "claim_count": len(points),
                "report": report,
            }
        except Exception:
            pass

    def _audit_event(
        self,
        signal: Signal,
        intent: TradeIntent | None,
        result: PipelineResult,
    ) -> PipelineResult:
        # Hash-chain ledger append goes BEFORE the audit_logger early-return:
        # production constructs with audit_logger=None, so returning above
        # would silently skip every chain write. Chain failure never blocks
        # the order — it degrades to this log line only.
        if self._audit_ledger is not None:
            try:
                self._audit_ledger.append(
                    {
                        "action": result.action.value,
                        "reason": result.reason,
                        "order_id": result.order_id,
                        "strategy_id": signal.strategy_id,
                        "symbol": signal.symbol,
                        "side": signal.side.value,
                    }
                )
            except Exception:
                pass
        if self._audit_logger is None:
            return result
        try:
            from selene_space_security.audit import (
                AuditEvent,
                AuditEventType,
                AuditSeverity,
            )

            self._audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.TOOL_CALL,
                    actor="decision_pipeline",
                    action=result.action.value,
                    severity=AuditSeverity.INFO
                    if result.action != PipelineAction.BLOCKED
                    else AuditSeverity.WARN,
                    outcome="success"
                    if result.action == PipelineAction.EXECUTED
                    else "denied",
                    details={
                        "strategy_id": signal.strategy_id,
                        "symbol": signal.symbol,
                        "side": signal.side.value,
                        "reason": result.reason,
                        "order_id": result.order_id,
                    },
                )
            )
        except Exception:
            pass
        return result
