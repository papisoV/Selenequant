"""Resilience locks for selene_space_core.resilience.circuit_breaker:
state machine transitions CLOSED→OPEN→HALF_OPEN→CLOSED, call gating, reset."""

import asyncio

import pytest

from selene_space_core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


async def _good() -> str:
    return "fine"


async def _bad() -> str:
    raise RuntimeError("kaboom")


def test_fresh_breaker_begins_closed():
    assert CircuitBreaker().state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_successful_call_passes_through_and_stays_closed():
    probe = CircuitBreaker(failure_threshold=3)
    assert await probe.call(_good) == "fine"
    assert probe.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_reaching_failure_count_opens_the_gate():
    probe = CircuitBreaker(failure_threshold=2)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await probe.call(_bad)
    assert probe.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_breaker_blocks_even_good_calls():
    probe = CircuitBreaker(failure_threshold=1, recovery_timeout=100)
    with pytest.raises(RuntimeError):
        await probe.call(_bad)
    with pytest.raises(CircuitOpenError):
        await probe.call(_good)


@pytest.mark.asyncio
async def test_wait_out_timeout_transitions_to_half_open():
    probe = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    with pytest.raises(RuntimeError):
        await probe.call(_bad)

    await asyncio.sleep(0.05)
    assert probe.state is CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_success_recloses_the_circuit():
    probe = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    with pytest.raises(RuntimeError):
        await probe.call(_bad)

    await asyncio.sleep(0.05)
    assert await probe.call(_good) == "fine"
    assert probe.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_reset_forces_back_to_closed():
    probe = CircuitBreaker(failure_threshold=1)
    with pytest.raises(RuntimeError):
        await probe.call(_bad)

    probe.reset()
    assert probe.state is CircuitState.CLOSED
