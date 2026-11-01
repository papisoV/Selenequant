"""Attempt-bounded execution of an async callable with scheduled pauses.

The runner invokes *fn* and, whenever it raises, consults the optional
should_retry predicate and the backoff schedule before sleeping and calling
fn again. The original exception object is always what the caller finally
sees -- nothing is wrapped or chained.
"""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from selene_space_core.resilience.backoff import BackoffPolicy

_R = TypeVar("_R")

_log = logging.getLogger(__name__)


async def retry_async(
    fn: Callable[[], Awaitable[_R]],
    *,
    max_attempts: int = 3,
    policy: BackoffPolicy | None = None,
    should_retry: Callable[[Exception], bool] | None = None,
) -> _R:
    """Call ``await fn()`` until it succeeds or the attempt budget runs out.

    Termination paths:
      * fn returns -> its value comes straight back;
      * the budget is spent -> the triggering exception propagates as-is;
      * should_retry(exc) is False -> the exception propagates immediately,
        without consuming another slot or sleeping.
    """
    schedule = policy if policy is not None else BackoffPolicy()
    # Degenerate budgets still get exactly one call (matching historical
    # behavior): a zero or negative budget behaves like 1.
    budget = max(1, max_attempts)

    # remaining counts calls still allowed including the one about to happen;
    # spent indexes the pause schedule (first retry waits schedule slot 0).
    remaining = budget
    spent = 0
    while remaining > 0:
        try:
            return await fn()
        except Exception as exc:
            remaining -= 1
            if remaining == 0:
                raise
            if should_retry is not None and not should_retry(exc):
                raise
            pause_for_this_round = schedule.calculate_delay(spent)
            _log.debug(
                "call failed (%r); %.3fs before retry %d of %d",
                exc,
                pause_for_this_round,
                spent + 1,
                budget - 1,
            )
            spent += 1
            await asyncio.sleep(pause_for_this_round)
    raise RuntimeError("unreachable: retry loop exited without returning")
