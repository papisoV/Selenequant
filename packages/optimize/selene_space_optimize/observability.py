"""Observability helpers for selene_space_optimize.

Provides:
- `log_compute()` — structured log emission for compute events (provider/action/duration/metrics)
- `mlflow_log()` — optional MLflow tracking (no-op if mlflow not installed)

Design aligned with packages/core/selene_space_core/logging.py redaction patterns.
Heavy deps (mlflow) are lazy-imported for graceful degradation.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("selene_space_optimize.observability")


def log_compute(
    *,
    provider: str,
    action: str,
    duration_ms: float,
    success: bool,
    metrics_keys: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Emit a structured compute event.

    Args:
        provider: Provider id (e.g. "ppo", "mean_variance")
        action: Action name (e.g. "train", "max_sharpe")
        duration_ms: Wall-clock duration in milliseconds
        success: Whether compute succeeded
        metrics_keys: Metric keys returned (for observability without PII)
        error: Error message if failed
    """
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
        logger.info("optimize.compute.ok", extra=extra)
    else:
        logger.warning("optimize.compute.fail", extra=extra)


@contextmanager
def track_compute(
    *,
    provider: str,
    action: str,
) -> Iterator[dict[str, Any]]:
    """Context manager that tracks compute duration and outcome.

    Usage:
        with track_compute(provider="ppo", action="train") as ctx:
            report = await provider.compute(...)
            ctx["metrics_keys"] = list(report.metrics.keys())
            ctx["success"] = True
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


def mlflow_log(
    *,
    run_name: str | None = None,
    metrics: dict[str, float] | None = None,
    params: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
) -> bool:
    """Log to MLflow. Returns True if logged, False if mlflow unavailable.

    No-op (returns False) when mlflow is not installed — providers should
    not fail just because MLflow tracking is not configured.
    """
    try:
        import mlflow  # noqa: PLC0415
    except ImportError:
        return False

    try:
        if run_name:
            mlflow.start_run(run_name=run_name)
        if params:
            for k, v in params.items():
                mlflow.log_param(k, v)
        if metrics:
            for k, v in metrics.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    mlflow.log_metric(k, float(v))
        if tags:
            for k, v in tags.items():
                mlflow.set_tag(k, v)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mlflow_log failed: %s", exc)
        return False
    finally:
        try:
            mlflow.end_run()
        except Exception:  # noqa: BLE001
            pass
