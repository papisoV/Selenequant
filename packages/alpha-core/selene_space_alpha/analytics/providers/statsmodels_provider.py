"""Statsmodels provider — statistical modeling (regression, cointegration, ARIMA).

Wraps statsmodels (https://www.statsmodels.org/).
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


class StatsModelsProvider:
    """Async statsmodels analytics provider."""

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
            import statsmodels  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("statsmodels not installed: %s", exc)
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
        if action not in {"cointegration", "adf", "ols_regression", "arima"}:
            raise KeyError(f"Unknown action: {action}")

        if action == "cointegration":
            return self._cointegration(prices, params)
        if action == "adf":
            return self._adf_stationarity(returns, prices, params)
        if action == "ols_regression":
            return self._ols(returns, benchmark, params)
        if action == "arima":
            return self._arima(returns, prices, params)
        raise KeyError(action)

    def _cointegration(
        self, prices: pd.DataFrame | pd.Series | None, params: dict[str, Any] | None
    ) -> AnalyticsReport:
        if prices is None or not isinstance(prices, pd.DataFrame) or prices.shape[1] < 2:
            raise ValueError("cointegration requires DataFrame with >=2 columns")
        from statsmodels.tsa.stattools import coint  # noqa: PLC0415
        a, b = prices.iloc[:, 0], prices.iloc[:, 1]
        try:
            score, p_value, crit = coint(a, b)
            metrics = {
                "coint_score": round(float(score), 6),
                "p_value": round(float(p_value), 6),
                "crit_1pct": round(float(crit[0]), 6),
                "crit_5pct": round(float(crit[1]), 6),
                "crit_10pct": round(float(crit[2]), 6),
                "cointegrated": bool(p_value < 0.05),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("coint failed: %s", exc)
            metrics = {"error": str(exc)}
        return AnalyticsReport(
            provider="statsmodels",
            kind=AnalyticsReportKind.STATISTICS,
            title="Engle-Granger Cointegration Test",
            metrics=metrics,
            computed_at=datetime.now(UTC),
        )

    def _adf_stationarity(
        self,
        returns: pd.Series | None,
        prices: pd.Series | pd.DataFrame | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        from statsmodels.tsa.stattools import adfuller  # noqa: PLC0415
        series = returns if returns is not None else prices
        if series is None:
            raise ValueError("adf requires returns or prices")
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        try:
            result = adfuller(series.dropna())
            metrics = {
                "adf_stat": round(float(result[0]), 6),
                "p_value": round(float(result[1]), 6),
                "stationary": bool(result[1] < 0.05),
                "n_lags": int(result[2]),
                "n_obs": int(result[3]),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("adf failed: %s", exc)
            metrics = {"error": str(exc)}
        return AnalyticsReport(
            provider="statsmodels",
            kind=AnalyticsReportKind.STATISTICS,
            title="ADF Stationarity Test",
            metrics=metrics,
            computed_at=datetime.now(UTC),
        )

    def _ols(
        self,
        returns: pd.Series | pd.DataFrame | None,
        benchmark: pd.Series | pd.DataFrame | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        from statsmodels.api import OLS, add_constant  # noqa: PLC0415
        if returns is None or benchmark is None:
            raise ValueError("ols requires returns + benchmark")
        y = returns if isinstance(returns, pd.Series) else returns.iloc[:, 0]
        x = benchmark if isinstance(benchmark, pd.Series) else benchmark.iloc[:, 0]
        df = pd.concat([y, x], axis=1, keys=["y", "x"]).dropna()
        try:
            model = OLS(df["y"], add_constant(df["x"])).fit()
            metrics = {
                "alpha": round(float(model.params["const"]), 6),
                "beta": round(float(model.params["x"]), 6),
                "r_squared": round(float(model.rsquared), 6),
                "p_alpha": round(float(model.pvalues["const"]), 6),
                "p_beta": round(float(model.pvalues["x"]), 6),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("ols failed: %s", exc)
            metrics = {"error": str(exc)}
        return AnalyticsReport(
            provider="statsmodels",
            kind=AnalyticsReportKind.STATISTICS,
            title="OLS Regression (returns ~ benchmark)",
            metrics=metrics,
            computed_at=datetime.now(UTC),
        )

    def _arima(
        self,
        returns: pd.Series | pd.DataFrame | None,
        prices: pd.Series | pd.DataFrame | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        from statsmodels.tsa.arima.model import ARIMA  # noqa: PLC0415
        series = returns if returns is not None else prices
        if series is None:
            raise ValueError("arima requires returns or prices")
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        order = tuple((params or {}).get("order", (1, 0, 1)))
        try:
            model = ARIMA(series.dropna(), order=order).fit()
            forecast = model.forecast(steps=int((params or {}).get("steps", 10)))
            metrics = {
                "order": list(order),
                "aic": round(float(model.aic), 6),
                "bic": round(float(model.bic), 6),
            }
            data = forecast.to_frame("forecast") if hasattr(forecast, "to_frame") else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("arima failed: %s", exc)
            metrics = {"error": str(exc)}
            data = None
        return AnalyticsReport(
            provider="statsmodels",
            kind=AnalyticsReportKind.FORECAST,
            title=f"ARIMA{order} Forecast",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["cointegration", "adf", "ols_regression", "arima"]

    def describe_action(self, action: str) -> str:
        return {
            "cointegration": "Engle-Granger cointegration test (2 series)",
            "adf": "Augmented Dickey-Fuller stationarity test",
            "ols_regression": "OLS regression: returns ~ benchmark",
            "arima": "ARIMA forecast",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("statsmodels", lambda cfg: StatsModelsProvider(cfg))


register()
