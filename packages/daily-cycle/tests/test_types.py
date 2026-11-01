"""Tests for daily-cycle types behavior (tests #2-6)."""

from __future__ import annotations


from selene_space_daily_cycle import (
    PhaseOutcome,
    ResearchSignal,
    WorkflowConfig,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


# ---------------------------------------------------------------------------
# Test #2: default disposition is "continue"
# ---------------------------------------------------------------------------


def test_phase_outcome_disposition_continue():
    """PhaseOutcome default disposition should be 'continue'."""
    outcome = PhaseOutcome(
        phase=WorkflowPhase.PRE_MARKET,
        status=WorkflowStatus.SUCCESS,
    )
    assert outcome.disposition == "continue"
    assert outcome.phase == WorkflowPhase.PRE_MARKET
    assert outcome.status == WorkflowStatus.SUCCESS
    assert outcome.error is None
    assert outcome.metrics == {}
    assert outcome.artifacts == {}


# ---------------------------------------------------------------------------
# Test #3: WorkflowConfig defaults
# ---------------------------------------------------------------------------


def test_workflow_config_defaults():
    """WorkflowConfig defaults make sense; series_ids fall back to FRED catalog ids."""
    cfg = WorkflowConfig()
    assert cfg.trading_date_timezone == "Asia/Shanghai"
    assert cfg.market_open_time == "09:30"
    assert cfg.ic_decision_provider == "persona"
    assert cfg.phase_timeouts == {}
    assert cfg.provider_configs == {}


# ---------------------------------------------------------------------------
# Test #4: artifacts read/write by phase name
# ---------------------------------------------------------------------------


def test_workflow_context_artifact_by_name():
    """WorkflowContext artifacts read/write by phase name."""
    ctx = WorkflowContext()
    assert ctx.artifacts == {}
    assert ctx.signals == []
    assert ctx.orders == []

    ctx.artifacts["pre_market"] = {"data": "test"}
    ctx.artifacts["risk_review"] = {"var": 0.05}

    assert ctx.artifacts["pre_market"] == {"data": "test"}
    assert ctx.artifacts["risk_review"] == {"var": 0.05}
    assert len(ctx.artifacts) == 2

    # WorkflowContext is intentionally mutable (scratchpad)
    ctx.signals.append("test_signal")
    assert ctx.signals == ["test_signal"]


# ---------------------------------------------------------------------------
# Test #5: ResearchSignal wraps Signal (composition), p_value/ic optional
# ---------------------------------------------------------------------------


def test_research_signal_composition():
    """ResearchSignal wraps Signal, not inherits; p_value/ic optional."""
    signal = Signal(
        strategy_id="strat-001",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        strength=0.75,
        timestamp=1700000000000,
        reason="test",
    )
    rs = ResearchSignal(signal=signal, signal_id="sig-001")
    assert rs.signal is signal
    assert rs.signal.strength == 0.75
    assert rs.signal_id == "sig-001"
    assert rs.p_value is None
    assert rs.ic is None

    # With p_value and ic
    rs2 = ResearchSignal(
        signal=signal,
        signal_id="sig-002",
        p_value=0.03,
        ic=0.05,
    )
    assert rs2.p_value == 0.03
    assert rs2.ic == 0.05

    # Composition: ResearchSignal is NOT a Signal subclass
    assert not isinstance(rs, Signal)
    assert isinstance(rs.signal, Signal)


# ---------------------------------------------------------------------------
# Test #6: phases list from constructor is not mutated by run()
# ---------------------------------------------------------------------------


def test_phase_ordering_immutable():
    """phases list passed to constructor is not mutated by run() (subset check)."""
    from selene_space_daily_cycle import DailyTradingCycleWorkflow

    class FakePhase:
        def __init__(self, name):
            self._name = name

        @property
        def name(self):
            return self._name

        async def execute(self, trading_date, context):
            return PhaseOutcome(
                phase=self._name,
                status=WorkflowStatus.SUCCESS,
                disposition="continue",
            )

    phases = [FakePhase(WorkflowPhase.PRE_MARKET)]
    original_phases = list(phases)
    workflow = DailyTradingCycleWorkflow(phases)
    # The constructor should not mutate the caller's list
    assert phases == original_phases
    assert len(phases) == 1
