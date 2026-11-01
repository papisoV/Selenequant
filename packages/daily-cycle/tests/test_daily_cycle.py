"""Orchestrator unit tests for DailyTradingCycleWorkflow (tests #7-14)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from selene_space_daily_cycle import (
    DailyTradingCycleWorkflow,
    PhaseOutcome,
    WorkflowConfig,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)


# ---------------------------------------------------------------------------
# Fake phase for testing
# ---------------------------------------------------------------------------


class FakePhase:
    """Configurable fake phase for orchestrator tests."""

    def __init__(
        self,
        name: WorkflowPhase,
        disposition: str = "continue",
        status: WorkflowStatus = WorkflowStatus.SUCCESS,
        delay: float = 0.0,
        error: Exception | None = None,
        executed_flag: list | None = None,
    ):
        self._name = name
        self._disposition = disposition
        self._status = status
        self._delay = delay
        self._error = error
        self._executed_flag = executed_flag

    @property
    def name(self) -> WorkflowPhase:
        return self._name

    async def execute(self, trading_date: date, context: WorkflowContext) -> PhaseOutcome:
        if self._executed_flag is not None:
            self._executed_flag.append(True)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return PhaseOutcome(
            phase=self._name,
            status=self._status,
            disposition=self._disposition,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Test #7: halt_urgent stops loop, downstream not executed, status=HALTED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cycle_halt_urgent_stops():
    """halt_urgent disposition breaks loop, downstream not executed, status=HALTED."""
    executed: list = []
    p1 = FakePhase(WorkflowPhase.PRE_MARKET, disposition="halt_urgent", executed_flag=executed)
    p2 = FakePhase(WorkflowPhase.RISK_REVIEW, executed_flag=executed)
    p3 = FakePhase(WorkflowPhase.IC_DECISION, executed_flag=executed)

    workflow = DailyTradingCycleWorkflow([p1, p2, p3])
    result = await workflow.run(date(2026, 7, 26))

    assert result.status == WorkflowStatus.HALTED
    assert result.halted_at == WorkflowPhase.PRE_MARKET
    assert len(executed) == 1  # only p1 executed
    assert len(result.phases) == 1


# ---------------------------------------------------------------------------
# Test #8: skip_remaining marks remaining phases as SKIPPED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cycle_skip_remaining_marks_skipped():
    """skip_remaining marks remaining phases as SKIPPED."""
    executed: list = []
    p1 = FakePhase(WorkflowPhase.PRE_MARKET, disposition="skip_remaining", executed_flag=executed)
    p2 = FakePhase(WorkflowPhase.RISK_REVIEW, executed_flag=executed)
    p3 = FakePhase(WorkflowPhase.IC_DECISION, executed_flag=executed)

    workflow = DailyTradingCycleWorkflow([p1, p2, p3])
    result = await workflow.run(date(2026, 7, 26))

    # p1 executed, p2/p3 marked SKIPPED
    assert len(executed) == 1
    assert len(result.phases) == 3
    assert result.phases[0].status == WorkflowStatus.SUCCESS
    assert result.phases[1].status == WorkflowStatus.SKIPPED
    assert result.phases[2].status == WorkflowStatus.SKIPPED
    # No halt_* disposition -> SUCCESS (all phases SUCCESS or SKIPPED)
    assert result.status == WorkflowStatus.SUCCESS
    assert result.halted_at is None


# ---------------------------------------------------------------------------
# Test #9: phase timeout produces FAILED with halt_urgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cycle_phase_timeout():
    """Phase taking > timeout produces FAILED outcome with halt_urgent disposition."""
    p1 = FakePhase(WorkflowPhase.PRE_MARKET, delay=0.5)
    p2 = FakePhase(WorkflowPhase.RISK_REVIEW)

    cfg = WorkflowConfig(
        phase_timeouts={WorkflowPhase.PRE_MARKET: 0.05},
    )
    workflow = DailyTradingCycleWorkflow([p1, p2], cfg)
    result = await workflow.run(date(2026, 7, 26))

    assert result.status == WorkflowStatus.HALTED
    assert result.halted_at == WorkflowPhase.PRE_MARKET
    assert result.phases[0].status == WorkflowStatus.FAILED
    assert result.phases[0].disposition == "halt_urgent"
    assert "timed out" in (result.phases[0].error or "").lower()


# ---------------------------------------------------------------------------
# Test #10: partial success (3 success + 2 fail) -> FAILED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cycle_partial_success():
    """3 success + 2 fail -> WorkflowStatus.FAILED, all outcomes recorded."""
    # Use exceptions so disposition becomes halt_urgent (and loop breaks on first fail)
    # To test "all outcomes recorded" we need failures that don't break the loop.
    # We simulate failures with status=FAILED but disposition=continue.
    p1 = FakePhase(WorkflowPhase.PRE_MARKET, status=WorkflowStatus.SUCCESS)
    p2 = FakePhase(WorkflowPhase.RISK_REVIEW, status=WorkflowStatus.FAILED, disposition="continue")
    p3 = FakePhase(WorkflowPhase.IC_DECISION, status=WorkflowStatus.SUCCESS)
    p4 = FakePhase(WorkflowPhase.EXECUTION, status=WorkflowStatus.FAILED, disposition="continue")
    p5 = FakePhase(WorkflowPhase.POST_MARKET, status=WorkflowStatus.SUCCESS)

    workflow = DailyTradingCycleWorkflow([p1, p2, p3, p4, p5])
    result = await workflow.run(date(2026, 7, 26))

    assert len(result.phases) == 5
    # No halt_* dispositions -> not HALTED
    # But not all SUCCESS -> FAILED
    assert result.status == WorkflowStatus.FAILED
    assert result.halted_at is None


# ---------------------------------------------------------------------------
# Test #11: phases passed via constructor (injectable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cycle_injectable_phases():
    """Phases passed via constructor (not hardcoded); custom phases can be injected."""
    p1 = FakePhase(WorkflowPhase.PRE_MARKET)
    p2 = FakePhase(WorkflowPhase.POST_MARKET)

    # Subset of phases is valid (constructor injects exactly what's passed)
    workflow = DailyTradingCycleWorkflow([p1, p2])
    assert len(workflow._phases) == 2
    assert workflow._phases[0] is p1
    assert workflow._phases[1] is p2

    result = await workflow.run(date(2026, 7, 26))
    assert result.status == WorkflowStatus.SUCCESS


# ---------------------------------------------------------------------------
# Test #12: halt_graceful -> HALTED, downstream still execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cycle_halt_graceful_status():
    """halt_graceful on phase 3 -> WorkflowStatus.HALTED, downstream phases still execute."""
    executed: list = []
    p1 = FakePhase(WorkflowPhase.PRE_MARKET, executed_flag=executed)
    p2 = FakePhase(WorkflowPhase.RISK_REVIEW, executed_flag=executed)
    p3 = FakePhase(
        WorkflowPhase.IC_DECISION,
        disposition="halt_graceful",
        executed_flag=executed,
    )
    p4 = FakePhase(WorkflowPhase.EXECUTION, executed_flag=executed)
    p5 = FakePhase(WorkflowPhase.POST_MARKET, executed_flag=executed)

    workflow = DailyTradingCycleWorkflow([p1, p2, p3, p4, p5])
    result = await workflow.run(date(2026, 7, 26))

    # All 5 phases executed (halt_graceful does NOT break the loop)
    assert len(executed) == 5
    assert len(result.phases) == 5
    # Final status HALTED (not SUCCESS) even though all phases executed
    assert result.status == WorkflowStatus.HALTED
    assert result.halted_at is None  # halt_graceful does not set halted_at


# ---------------------------------------------------------------------------
# Test #13: valid subset phases accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_ordering_valid_subset():
    """Valid subset phases (e.g. [PRE_MARKET, EXECUTION]) accepted."""
    p1 = FakePhase(WorkflowPhase.PRE_MARKET)
    p4 = FakePhase(WorkflowPhase.EXECUTION)
    # Should not raise
    workflow = DailyTradingCycleWorkflow([p1, p4])
    assert len(workflow._phases) == 2

    result = await workflow.run(date(2026, 7, 26))
    assert result.status == WorkflowStatus.SUCCESS


# ---------------------------------------------------------------------------
# Test #14: phases out of canonical order -> ValueError
# ---------------------------------------------------------------------------


def test_phase_ordering_invalid_order():
    """Phases out of canonical order (e.g. [EXECUTION, PRE_MARKET]) -> ValueError."""
    p4 = FakePhase(WorkflowPhase.EXECUTION)
    p1 = FakePhase(WorkflowPhase.PRE_MARKET)
    with pytest.raises(ValueError, match="canonical order"):
        DailyTradingCycleWorkflow([p4, p1])
