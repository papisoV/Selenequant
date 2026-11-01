"""Daily trading cycle workflow types.

Implements renaissance DailyTradingCycleWorkflow concept natively in selene:
- WorkflowPhase enum (5 phases)
- WorkflowStatus enum (with HALTED for halted workflows)
- PhaseOutcome with explicit disposition field
- WorkflowResult with halted_at field
- PreMarketReport / RiskReviewReport / ICDecisionReport / ExecutionReport / PostMarketReport
- ResearchSignal (composition over inheritance -- wraps Signal)
- WorkflowConfig dataclass
- WorkflowContext (intentionally mutable scratchpad)
- ICDecisionProvider Protocol
- Phase Protocol
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from selene_space_types.strategy import Signal
from selene_space_types.trading import Order


# ---------------------------------------------------------------------------
# Workflow phases
# ---------------------------------------------------------------------------


class WorkflowPhase(str, Enum):
    """5 phases of the daily trading cycle, in canonical order."""

    PRE_MARKET = "pre_market"
    RISK_REVIEW = "risk_review"
    IC_DECISION = "ic_decision"
    EXECUTION = "execution"
    POST_MARKET = "post_market"


class WorkflowStatus(str, Enum):
    """Workflow / phase status.

    HALTED is a dedicated status for workflows that stopped early via
    halt_graceful or halt_urgent disposition. This is distinct from
    FAILED (exception) and SKIPPED (intentionally bypassed).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    HALTED = "halted"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# PhaseOutcome -- explicit disposition field (replaces overloaded StepResult)
# ---------------------------------------------------------------------------


Disposition = Literal["continue", "halt_graceful", "halt_urgent", "skip_remaining"]


@dataclass(frozen=True)
class PhaseOutcome:
    """Result of a single phase execution.

    `disposition` tells the orchestrator what to do next:
    - "continue" -> proceed to next phase
    - "halt_graceful" -> flag as halted but allow downstream phases to run
      (final WorkflowResult.status = HALTED)
    - "halt_urgent" -> stop immediately, no more phases
    - "skip_remaining" -> skip all downstream phases (mark them SKIPPED)
    """

    phase: WorkflowPhase
    status: WorkflowStatus
    disposition: Disposition = "continue"
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime = field(default_factory=datetime.utcnow)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class WorkflowResult:
    """Final result of running the daily cycle."""

    workflow_id: str
    trading_date: date
    phases: list[PhaseOutcome]
    started_at: datetime
    ended_at: datetime
    status: WorkflowStatus
    halted_at: WorkflowPhase | None = None  # which phase triggered the halt


# ---------------------------------------------------------------------------
# Report dataclasses -- concrete outputs for each phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreMarketReport:
    """Phase 1 output: macro indicators and overnight signals."""

    macro_indicators: dict[str, float]
    overnight_signals: list[
        Any
    ]  # list[ResearchSignal], forward-ref to avoid circular import
    data_quality: Literal["clean", "degraded", "missing"]


@dataclass(frozen=True)
class RiskReviewReport:
    """Phase 2 output: risk assessment from risk-engine provider."""

    var_estimate: float
    position_limits_ok: bool
    kill_switch_triggered: bool


@dataclass(frozen=True)
class ICDecisionReport:
    """Phase 3 output: approved/rejected signals with IC scores."""

    approved: list[Any]  # list[ResearchSignal]
    rejected: list[Any]  # list[ResearchSignal]
    ic_scores: dict[str, float]  # signal_id -> IC score


@dataclass(frozen=True)
class ExecutionReport:
    """Phase 4 output: orders placed via paper engine (ACCEPTED, not FILLED)."""

    orders: list[Order]


@dataclass(frozen=True)
class PostMarketReport:
    """Phase 5 output: daily PnL summary and lessons learned."""

    pnl_summary: dict[str, float]
    lessons: list[str]
    memory_updated: bool


# ---------------------------------------------------------------------------
# ResearchSignal -- composition wrapper (NOT inheritance)
# ---------------------------------------------------------------------------


class ResearchSignal(BaseModel):
    """Signal with IC-evaluation metadata.

    Uses composition over inheritance -- wraps Signal rather than extending it.
    Signal already has `strength: float` (ge=0, le=1), so there is no separate
    `confidence` field needed.

    New fields (not in base Signal):
    - p_value: statistical significance of the signal's predictive power
    - ic: information coefficient (correlation between signal and forward return)
    - signal_id: unique identifier for traceability across workflow phases
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    signal: Signal
    p_value: float | None = None
    ic: float | None = None
    signal_id: str


# ---------------------------------------------------------------------------
# WorkflowConfig -- concrete dataclass with all required fields
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowConfig:
    """Configuration for DailyTradingCycleWorkflow.

    Attributes:
        trading_date_timezone: IANA timezone string (e.g. "Asia/Shanghai")
        market_open_time: HH:MM format for market open (used for scheduling)
        phase_timeouts: per-phase timeout in seconds (None = no timeout)
        provider_configs: provider-specific overrides
            - provider_configs["macrodata"]["series_ids"]: list[str] -- which
              macro series to fetch in Phase 1 (default: ["DGS10", "CPIAUCSL"])
            - provider_configs["risk_engine"]["var_threshold"]: float -- VaR
              limit before kill_switch triggers
        ic_decision_provider: "persona" or "rd_loop"
    """

    trading_date_timezone: str = "Asia/Shanghai"
    market_open_time: str = "09:30"
    phase_timeouts: dict[WorkflowPhase, float | None] = field(default_factory=dict)
    provider_configs: dict[str, Any] = field(default_factory=dict)
    ic_decision_provider: Literal["persona", "rd_loop"] = "persona"


# ---------------------------------------------------------------------------
# WorkflowContext -- accumulates phase outputs by name
# ---------------------------------------------------------------------------


@dataclass
class WorkflowContext:
    """Mutable context bag that accumulates phase artifacts.

    Each phase reads/writes named slots. Examples:
        ctx.artifacts["pre_market"]   -> PreMarketReport
        ctx.artifacts["risk_review"]  -> RiskReviewReport
        ctx.artifacts["ic_decision"]  -> ICDecisionReport
        ctx.artifacts["execution"]    -> ExecutionReport

    **Design note (immutability exception):** WorkflowContext is intentionally
    mutable as a scratchpad shared across phases. PhaseOutcome is frozen
    (audit trail). This is a deliberate exception to the immutability rule,
    documented in the plan's design_decisions section.
    """

    artifacts: dict[str, Any] = field(default_factory=dict)
    signals: list[ResearchSignal] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)


# ---------------------------------------------------------------------------
# IC Decision Provider Protocol + Adapter Contracts
# ---------------------------------------------------------------------------


class ICDecisionProvider(Protocol):
    """Protocol for IC (Investment Committee) decision-making.

    Two implementations with explicit adapter contracts:
    - PersonaICDecisionProvider: wraps renintel persona_provider
    - RDLoopICDecisionProvider: wraps agentstudio rd_loop_provider

    Runtime choice via WorkflowConfig.ic_decision_provider.

    CRITICAL: The `decide()` signature is the ADAPTER interface, NOT the
    underlying provider's native `compute()` signature. Each implementation
    must map `decide()` args to the provider's `compute(*, action, task, params)`
    contract and parse the report back to `list[ResearchSignal]`.
    """

    async def decide(
        self, trading_date: date, signals: list[ResearchSignal]
    ) -> ICDecisionReport:
        """Review signals and return approved/rejected lists with IC scores."""
        ...


# ---------------------------------------------------------------------------
# Phase Protocol
# ---------------------------------------------------------------------------


class Phase(Protocol):
    """Protocol for a single phase of the daily trading cycle.

    Each phase receives the trading date and the shared WorkflowContext.
    Returns a PhaseOutcome with an explicit disposition telling the orchestrator
    what to do next.
    """

    @property
    def name(self) -> WorkflowPhase: ...

    async def execute(
        self, trading_date: date, context: WorkflowContext
    ) -> PhaseOutcome:
        """Execute this phase.

        Args:
            trading_date: The date this cycle is running for.
            context: Shared mutable bag; read/write artifacts by name.

        Returns:
            PhaseOutcome with disposition dictating next phase behavior.
        """
        ...
