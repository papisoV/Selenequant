"""Observability helpers for selene_space_daily_cycle.

Mirrors the track_compute pattern from selene_space_optimize,
selene_space_agentstudio, and selene_space_renintel.

API: context manager yields `ctx: dict[str, Any]` with keys:
  - ctx["success"] = True   (set by caller on success)
  - ctx["metrics_keys"] = [...]  (set by caller with metric names)
  - ctx["error"] = "msg"    (set by context manager on exception)

This is the SAME pattern used by all existing selene packages.
It is NOT a `.log()` method call.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("selene_space_daily_cycle.observability")


def log_compute(
    *,
    provider: str,
    action: str,
    duration_ms: float,
    success: bool,
    metrics_keys: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Emit a structured compute event."""
    extra = {
        "provider": provider,
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "success": success,
        "metrics_keys": metrics_keys or [],
    }
    if error:
        extra["error"] = error
    if success:
        logger.info("daily_cycle.compute.ok", extra=extra)
    else:
        logger.warning("daily_cycle.compute.fail", extra=extra)


@contextmanager
def track_compute(
    *,
    provider: str,
    action: str,
) -> Iterator[dict[str, Any]]:
    """Context manager that tracks compute duration and outcome.

    Yields a ctx dict. Callers set:
        ctx["success"] = True
        ctx["metrics_keys"] = ["metric_a", "metric_b"]

    On exception, ctx["error"] is set and the exception re-raises.
    """
    ctx: dict[str, Any] = {
        "success": False,
        "metrics_keys": [],
        "error": None,
    }
    start = time.perf_counter()
    try:
        yield ctx
    except Exception as exc:
        ctx["error"] = str(exc)
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        log_compute(
            provider=provider,
            action=action,
            duration_ms=duration_ms,
            success=ctx["success"] and ctx["error"] is None,
            metrics_keys=ctx["metrics_keys"],
            error=ctx["error"],
        )
