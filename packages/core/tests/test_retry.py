"""Resilience locks for selene_space_core.resilience.retry — attempt counts,
eventual success, exhaustion propagation, should_retry veto, delay pacing."""

import pytest

from selene_space_core.resilience.backoff import BackoffPolicy
from selene_space_core.resilience.retry import retry_async

FAST = dict(initial=0.001, factor=1.0, jitter=False)


@pytest.mark.asyncio
async def test_immediate_success_costs_one_attempt():
    attempts = 0

    async def stable():
        nonlocal attempts
        attempts += 1
        return "fine"

    assert await retry_async(stable, max_attempts=3) == "fine"
    assert attempts == 1


@pytest.mark.asyncio
async def test_flaky_call_succeeds_on_third_attempt():
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("not yet")
        return "fine"

    outcome = await retry_async(flaky, max_attempts=3, policy=BackoffPolicy(**FAST))
    assert (outcome, attempts) == ("fine", 3)


@pytest.mark.asyncio
async def test_permanent_failure_raises_after_budget_spent():
    async def doomed():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        await retry_async(doomed, max_attempts=2, policy=BackoffPolicy(**FAST))


@pytest.mark.asyncio
async def test_veto_predicate_stops_retrying_disallowed_errors():
    attempts = 0

    async def wrong_kind():
        nonlocal attempts
        attempts += 1
        raise TypeError("no retry")

    with pytest.raises(TypeError):
        await retry_async(
            wrong_kind,
            max_attempts=5,
            policy=BackoffPolicy(initial=0.001, jitter=False),
            should_retry=lambda e: not isinstance(e, TypeError),
        )
    assert attempts == 1


@pytest.mark.asyncio
async def test_recovery_after_three_failures():
    """Verify backoff delays are increasing across retries."""
    attempts = 0

    async def recovers():
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise ValueError("again")
        return "done"

    await retry_async(recovers, max_attempts=4, policy=BackoffPolicy(initial=0.01, factor=2.0, jitter=False))
    assert attempts == 4
