"""Liveness of the long-running asyncio tasks started in the app lifespan.

asyncio only surfaces a task that finished unexpectedly at garbage-collection
time, as a log line nobody reads. This module turns that into a health
component, so ``/health/ready`` reports a loop that stopped rather than
reporting the process is fine because it is still answering HTTP.

Scope — what this catches and what it does not:

* **caught**: the coroutine finished. Either it returned early (a precondition
  swallowed it and it will never tick again) or it died from an exception no
  ``except`` caught.
* **not caught**: a task alive but wedged inside an ``await``. That is a
  different failure needing a per-loop heartbeat, and claiming otherwise would
  overstate what a task-state check measures.

Relationship to TSP's watchdog (``backend/app/watchdog.py``): its
``os._exit(70)`` relies on a supervisor that restarts the process. selene's
compose deployment has ``restart: unless-stopped``, but that fires on **exit**,
not on unhealthy — a task that stops does not make the container exit. So this
probe raises an alarm (503 from ``/health/ready``, which the container's
healthcheck polls) and does not self-heal. The alarm is the point: restarting
the process would not repair a loop that returned because, say, its symbol list
was empty; it would restart into the same state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Mapping
from typing import Any

from selene_space_core.diagnostics.health import HealthCheckResult, HealthRegistry

logger = logging.getLogger(__name__)

#: ``state`` key, display name, criticality.
#:
#: *critical* — the app is wrong if it stops. Its end (return or exception) is
#: reported as ``unhealthy``.
#:
#: *best_effort* — a warm-up or seed whose docstring already says a failure is
#: survivable. Its clean return is success and stays silent; only an exception
#: is reported, as ``degraded``.
_TASKS: tuple[tuple[str, str, bool], ...] = (
    ("_mission_worker_task", "mission-worker", True),
    ("_revisit_watch_task", "revisit-watcher", True),
    ("_options_poll_task", "options-poller", True),
    ("_kline_sweep_task", "kline-gap-sweeper", True),
    ("_astock_daily_sync_task", "astock-daily-sync", True),
    ("_astock_daily_all_task", "astock-daily-all", True),
    ("_autosave_task", "paper-autosave", True),
    ("_fund_ingest_alert_task", "fund-ingest-premium-alert", True),
    ("_pa_trade_alert_task", "pa-trade-alert", True),
    # A4: rule evaluation. Its end means the app keeps answering HTTP while
    # silently no longer evaluating any user rule — the worst possible state
    # for a monitoring feature, so it is critical rather than best-effort.
    ("_alert_rule_loop_task", "alert-rule-loop", True),
    # A4 批次 2: the large-trade consumer. Same argument — the loop task above
    # only polls price/indicator, so this one dying loses a whole rule type
    # while /health still looks fine.
    ("_alert_large_trade_task", "alert-large-trade", True),
    # One-shot warm-ups and seeds: finishing is their designed end state.
    ("_kline_sync_task", "kline-startup-sync", False),
    ("_subscribe_task", "subscribe-default-symbols", False),
    ("_astock_snapshot_seed_task", "astock-snapshot-seed", False),
    ("_astock_ladder_warmup_task", "astock-ladder-warmup", False),
    ("_ashare_warmup_task", "ashare-style-warmup", False),
    # These two were started without keeping the handle, which made their death
    # invisible by construction. Both swallow their own exceptions, so an
    # exception reaching the task means something outside their try block
    # broke — worth surfacing as degraded, not worth a 503.
    ("_live_engine_task", "live-engine-start", False),
    ("_binance_user_data_task", "binance-user-data-start", False),
)

#: ``_news_ingest_tasks`` is one state key holding three loops, all critical.
_NEWS_TASK_NAMES: tuple[str, ...] = (
    "news-collector",
    "news-tagger",
    "news-detector",
)

#: Longest error text carried in the health payload. A traceback would bloat
#: every health response; type and message are the actionable part and the full
#: traceback is already in the log.
_ERROR_CHARS = 300


def _task_name(task: Any, fallback: str) -> str:
    """Prefer the name given at ``create_task``; fall back to the state key."""
    try:
        return str(task.get_name())
    except Exception:  # noqa: BLE001
        return fallback


def _row(name: str, task: asyncio.Task, critical: bool) -> dict[str, Any] | None:
    """Describe a task that has ended; ``None`` while it is still running.

    A cancelled task is skipped, not reported: cancellation is how
    ``_shutdown_lifespan`` stops every one of these, and a dying process is
    not a health condition.
    """
    if task.cancelled():
        return None
    if not task.done():
        return None
    exc = task.exception()
    if exc is not None:
        return {
            "name": name,
            "critical": critical,
            "state": "failed",
            "error": f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS],
        }
    return {"name": name, "critical": critical, "state": "returned"}


def _expected_to_run(state: Mapping[str, Any], name: str) -> bool:
    """Whether the loop's own precondition lets it run in this deployment.

    Every long-running loop here returns early when a dependency is missing,
    and those returns are the designed end state, not a failure. So each entry
    below mirrors *its own loop's* guards rather than a shared approximation:
    the guards differ (``astock-daily-all`` checks for a store the sweeper does
    not), and folding them together is how a legitimate return becomes a 503.
    """
    if name == "astock-daily-sync":
        # The loop logs "no reachable A-share symbols; loop idle" and returns,
        # and returns again when there is no kline_manager. Reachability is a
        # local in ``_init_engines_and_runtime``, so the registration site
        # records both conditions in one flag.
        return bool(state.get("_astock_daily_sync_expected"))
    if name == "options-poller":
        # Returns when either the service or the fetcher is absent.
        return (
            state.get("market_svc") is not None
            and state.get("options_fetcher") is not None
        )
    if name == "kline-gap-sweeper":
        # Guards on the manager alone: an unusable store shows up as a failed
        # round, not a return.
        return state.get("kline_manager") is not None
    if name == "astock-daily-all":
        # A second guard the sweeper does not have: with no DB there is nowhere
        # to write, so the loop returns rather than logging failures forever.
        # Reached through the manager because that is how the loop gets it.
        return getattr(state.get("kline_manager"), "_db", None) is not None
    return True


def _entries(state: Mapping[str, Any]) -> Iterator[tuple[str, Any, bool]]:
    """Yield ``(name, task, critical)`` for every task this app starts."""
    for key, name, critical in _TASKS:
        yield name, state.get(key), critical
    news = state.get("_news_ingest_tasks") or ()
    for fallback, task in zip(_NEWS_TASK_NAMES, news):
        yield fallback, task, True


def task_liveness_verdict(state: Mapping[str, Any]) -> HealthCheckResult:
    """Fold the started tasks into one health verdict.

    ``unhealthy`` when a critical task has ended — it will never tick again and
    nothing restarts it. ``degraded`` when a best-effort task raised, so the
    operator sees a failed warm-up without the readiness gate calling the app
    unserviceable.

    A ``None`` state entry means "not started in this deployment" (opt-out env,
    no storage, no registered source) and is skipped: the probe observes tasks
    that exist rather than policing what should exist.
    """
    unhealthy: list[dict[str, Any]] = []
    degraded: list[dict[str, Any]] = []

    for name, task, critical in _entries(state):
        if not isinstance(task, asyncio.Task):
            continue
        row = _row(_task_name(task, name), task, critical)
        if row is None:
            continue
        if row["state"] == "returned" and not critical:
            # A one-shot that finished cleanly did its job.
            continue
        expected = critical and _expected_to_run(state, name)
        if row["state"] == "returned" and critical and not expected:
            continue
        (unhealthy if critical else degraded).append(row)

    if unhealthy:
        status = "unhealthy"
    elif degraded:
        status = "degraded"
    else:
        status = "healthy"

    detail: dict[str, Any] = {}
    if unhealthy:
        detail["stopped"] = unhealthy
    if degraded:
        detail["failed"] = degraded
    return HealthCheckResult(
        name="task_liveness", status=status, latency_ms=0.0, detail=detail
    )


def task_liveness_probe(state: Mapping[str, Any]):
    """Build the async probe; logs only when the verdict changes.

    The container healthcheck polls every 30s and the frontend's startup gate
    reads the same registry, so per-call logging would bury the one transition
    that matters under a steady stream of "healthy".
    """
    last: str | None = None

    async def _probe() -> HealthCheckResult:
        nonlocal last
        result = task_liveness_verdict(state)
        if result.status != last:
            if result.status == "healthy":
                logger.info("task liveness recovered")
            else:
                logger.warning("task liveness %s: %s", result.status, result.detail)
            last = result.status
        return result

    return _probe


def register_task_liveness_probe(
    registry: HealthRegistry, state: Mapping[str, Any]
) -> None:
    """Register the ``task_liveness`` component on the shared registry."""
    registry.register_check("task_liveness", task_liveness_probe(state))
