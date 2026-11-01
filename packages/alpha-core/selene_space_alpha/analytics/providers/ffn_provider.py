"""FFN provider — financial functions (performance + risk).

Wraps ffn (https://github.com/pmorissette/ffn).
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


class FfnProvider:
    """Async FFN analytics provider."""

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
            import ffn  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("ffn not installed: %s", exc)
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
        if action not in {"performance_stats", "drawdown", "monthly_returns"}:
            raise KeyError(f"Unknown action: {action}")
        if prices is None and returns is None:
            raise ValueError("ffn requires prices or returns")
        import ffn  # noqa: PLC0415

        if prices is None:
            prices = (1 + returns).cumprod() if returns is not None else None
        assert prices is not None

        # ffn prefers a Series; if DataFrame with 1 col, take it.
        ps = prices.iloc[:, 0] if isinstance(prices, pd.DataFrame) else prices

        if action == "performance_stats":
            try:
                stats = ffn.calc_stats(ps)
                perf = {
                    "cagr": round(float(stats.cagr), 6),
                    "vol": round(float(stats.volatility), 6),
                    "sharpe": round(float(stats.sharpe), 6),
                    "sortino": round(float(stats.sortino), 6),
                    "max_drawdown": round(float(stats.max_drawdown), 6),
                    "calmar": round(float(stats.calmar), 6),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("ffn perf failed: %s", exc)
                perf = {"error": str(exc)}
            return AnalyticsReport(
                provider="ffn",
                kind=AnalyticsReportKind.PERFORMANCE,
                title="FFN Performance Stats",
                metrics=perf,
                computed_at=datetime.now(UTC),
            )
        if action == "drawdown":
            try:
                dd = ffn.to_drawdown_series(ps)
                perf = {
                    "max_drawdown": round(float(dd.min()), 6) if len(dd) else None,
                }
                data = dd.to_frame("drawdown")
            except Exception as exc:  # noqa: BLE001
                logger.warning("ffn drawdown failed: %s", exc)
                perf = {"error": str(exc)}
                data = None
            return AnalyticsReport(
                provider="ffn",
                kind=AnalyticsReportKind.RISK,
                title="FFN Drawdown",
                metrics=perf,
                data=data,
                computed_at=datetime.now(UTC),
            )
        # monthly_returns
        try:
            monthly = ps.resample("ME").last().pct_change().dropna()
            perf: dict[str, Any] = {"count": int(len(monthly)), "mean": round(float(monthly.mean()), 6)}
            data = monthly.to_frame("monthly_return")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ffn monthly failed: %s", exc)
            perf = {"error": str(exc)}
            data = None
        return AnalyticsReport(
            provider="ffn",
            kind=AnalyticsReportKind.PERFORMANCE,
            title="FFN Monthly Returns",
            metrics=perf,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["performance_stats", "drawdown", "monthly_returns"]

    def describe_action(self, action: str) -> str:
        return {
            "performance_stats": "CAGR/vol/sharpe/sortino/max_dd/calmar",
            "drawdown": "Drawdown series",
            "monthly_returns": "Monthly returns series",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("ffn", lambda cfg: FfnProvider(cfg))


register()
