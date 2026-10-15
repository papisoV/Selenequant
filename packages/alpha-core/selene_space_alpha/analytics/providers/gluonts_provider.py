"""GluonTS provider — probabilistic time-series forecasting.

Wraps gluonts (https://github.com/awslabs/gluonts). Heavy dependency — only
import on demand.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from selene_space_alpha.analytics.base import (
    AnalyticsProviderConfig,
    AnalyticsReport,
    AnalyticsReportKind,
)

logger = logging.getLogger(__name__)


class GluonTSProvider:
    """Async GluonTS analytics provider (P1)."""

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
            import gluonts  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("gluonts not installed: %s", exc)
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
        if action not in {"simple_forecast", "predict"}:
            raise KeyError(f"Unknown action: {action}")
        if prices is None and returns is None:
            raise ValueError("gluonts requires prices or returns")

        # GluonTS adds significant complexity; for the P1 stub we provide a
        # naive seasonal forecast as a placeholder until full integration.
        series = prices if prices is not None else returns
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        steps = int((params or {}).get("steps", 30))
        try:
            last_val = float(series.dropna().iloc[-1])
            # Naive: assume last value repeats (placeholder).
            forecast = pd.Series(
                [last_val] * steps,
                index=pd.date_range(
                    series.dropna().index[-1],
                    periods=steps + 1,
                    freq="D",
                )[1:],
            )
            metrics = {
                "method": "naive_placeholder",
                "steps": steps,
                "last_value": round(last_val, 6),
            }
            data = forecast.to_frame("forecast")
        except Exception as exc:  # noqa: BLE001
            logger.warning("gluonts forecast failed: %s", exc)
            metrics = {"error": str(exc)}
            data = None

        return AnalyticsReport(
            provider="gluonts",
            kind=AnalyticsReportKind.FORECAST,
            title=f"GluonTS Forecast ({action})",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
            notes="Placeholder forecast — full GluonTS integration deferred",
        )

    def list_actions(self) -> list[str]:
        return ["simple_forecast", "predict"]

    def describe_action(self, action: str) -> str:
        return {
            "simple_forecast": "Simple (naive) forecast placeholder",
            "predict": "Run prediction (naive placeholder)",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("gluonts", lambda cfg: GluonTSProvider(cfg))


register()
