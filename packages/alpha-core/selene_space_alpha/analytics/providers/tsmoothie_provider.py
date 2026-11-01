"""Tsmoothie provider — time-series smoothing.

Wraps tsmoothie (https://github.com/cerlymarco/tsmoothie).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from selene_space_alpha.analytics.base import (
    AnalyticsProviderConfig,
    AnalyticsReport,
    AnalyticsReportKind,
)

logger = logging.getLogger(__name__)


class TsmoothieProvider:
    """Async tsmoothie analytics provider."""

    def __init__(self, cfg: AnalyticsProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> AnalyticsProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        try:
            import tsmoothie  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("tsmoothie not installed: %s", exc)
            return False

    async def compute(
        self,
        *,
        action: str,
        returns: pd.DataFrame | pd.Series | None = None,
        prices: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> AnalyticsReport:
        if action not in {"exponential", "lowess", "kalman"}:
            raise KeyError(f"Unknown action: {action}")
        series = returns if returns is not None else prices
        if series is None:
            raise ValueError("tsmoothie requires returns or prices")
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        arr = series.dropna().to_numpy().reshape(1, -1).astype(float)

        if action == "exponential":
            from tsmoothie.smoother import ExponentialSmoother  # noqa: PLC0415
            window = int((params or {}).get("window", 10))
            try:
                sm = ExponentialSmoother(window_len=window, alpha=0.3)
                sm.smooth(arr)
                smoothed = sm.smooth_data[0]
                metrics = {"window": window, "method": "exponential"}
            except Exception as exc:  # noqa: BLE001
                logger.warning("exp smooth failed: %s", exc)
                smoothed = np.full(arr.shape[1], np.nan)
                metrics = {"error": str(exc)}
        elif action == "lowess":
            from tsmoothie.smoother import LowessSmoother  # noqa: PLC0415
            try:
                sm = LowessSmoother(smooth_fraction=0.2, iterations=2)
                sm.smooth(arr)
                smoothed = sm.smooth_data[0]
                metrics = {"method": "lowess", "smooth_fraction": 0.2}
            except Exception as exc:  # noqa: BLE001
                logger.warning("lowess failed: %s", exc)
                smoothed = np.full(arr.shape[1], np.nan)
                metrics = {"error": str(exc)}
        else:  # kalman
            from tsmoothie.smoother import KalmanSmoother  # noqa: PLC0415
            try:
                sm = KalmanSmoother(component="level_trend")
                sm.smooth(arr)
                smoothed = sm.smooth_data[0]
                metrics = {"method": "kalman"}
            except Exception as exc:  # noqa: BLE001
                logger.warning("kalman failed: %s", exc)
                smoothed = np.full(arr.shape[1], np.nan)
                metrics = {"error": str(exc)}

        idx = series.dropna().index
        data = pd.DataFrame(
            {"raw": series.dropna().to_numpy(), "smoothed": smoothed},
            index=idx[: len(smoothed)],
        )
        return AnalyticsReport(
            provider="tsmoothie",
            kind=AnalyticsReportKind.SMOOTHING,
            title=f"Tsmoothie: {action}",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["exponential", "lowess", "kalman"]

    def describe_action(self, action: str) -> str:
        return {
            "exponential": "Exponential smoothing",
            "lowess": "LOWESS locally weighted regression",
            "kalman": "Kalman filter (level + trend)",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("tsmoothie", lambda cfg: TsmoothieProvider(cfg))


register()
