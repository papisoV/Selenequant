"""Health checks: register named async probes, run them all, collect verdicts."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class HealthCheckResult:
    name: str
    status: str
    latency_ms: float
    detail: dict = field(default_factory=dict)


class HealthRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, Callable] = {}

    @property
    def check_names(self) -> list[str]:
        return list(self._checks.keys())

    def register_check(self, name: str, check: Callable) -> None:
        self._checks[name] = check

    async def run_all(self) -> list[HealthCheckResult]:
        results: list[HealthCheckResult] = []
        for name, check in self._checks.items():
            results.append(await self._run_one(name, check))
        return results

    async def _run_one(self, name: str, check: Callable) -> HealthCheckResult:
        started = time.monotonic()
        try:
            outcome = check()
            if inspect.isawaitable(outcome):
                outcome = await outcome
            latency_ms = (time.monotonic() - started) * 1000.0
            if isinstance(outcome, HealthCheckResult):
                if outcome.latency_ms == 0:
                    outcome.latency_ms = latency_ms
                return outcome
            if isinstance(outcome, dict):
                status = str(outcome.get("status", "healthy"))
                detail = {k: v for k, v in outcome.items() if k != "status"}
                return HealthCheckResult(
                    name=name,
                    status=status,
                    latency_ms=latency_ms,
                    detail=detail or outcome,
                )
            return HealthCheckResult(
                name=name, status="healthy", latency_ms=latency_ms, detail={}
            )
        except Exception as exc:  # a failing probe is a result, not a crash
            latency_ms = (time.monotonic() - started) * 1000.0
            return HealthCheckResult(
                name=name,
                status="unhealthy",
                latency_ms=latency_ms,
                detail={"error": str(exc)},
            )
