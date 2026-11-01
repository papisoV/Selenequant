"""QuantStats provider — portfolio performance/risk metrics + tearsheet.

Wraps the quantstats library (https://github.com/ranaroussi/quantstats) to
provide 50+ performance/risk metrics, drawdown analysis, and HTML tearsheet
generation. Inspired by a commercial analytics terminal's quantstats module
(clean-room reimplementation, no source code copied).

Lazy-imports quantstats inside healthcheck/compute so the package can be
imported even if quantstats is not installed (graceful degradation).
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


def _safe_float(val: Any) -> float | None:
    """Convert numpy/pandas scalar to JSON-safe Python float."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(v) or not pd.notna(v):
        return None
    import math
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, 6)


class QuantStatsProvider:
    """Async QuantStats analytics provider."""

    def __init__(self, cfg: AnalyticsProviderConfig) -> None:
        self._cfg = cfg
        self._qs: Any = None
        self._running = False

    @property
    def config(self) -> AnalyticsProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._qs is None:
            import quantstats as qs  # noqa: PLC0415
            self._qs = qs
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._qs = None

    async def healthcheck(self) -> bool:
        try:
            import quantstats  # noqa: PLC0415, F401
            return True
        except ImportError as exc:
            logger.warning("quantstats not installed: %s", exc)
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
        if self._qs is None:
            await self.start()
        assert self._qs is not None
        if action not in {"stats", "returns", "drawdown", "rolling", "cumulative", "distribution", "html_report"}:
            raise KeyError(f"Unknown action: {action}")
        if returns is None and prices is None:
            raise ValueError("quantstats requires returns or prices")

        # Convert prices → returns if only prices provided.
        if returns is None and prices is not None:
            returns = self._qs.utils.to_returns(prices)

        # QuantStats expects a Series for single-asset returns.
        if isinstance(returns, pd.DataFrame):
            if len(returns.columns) == 1:
                ret = returns.iloc[:, 0]
            else:
                # Aggregate to portfolio returns if weights provided.
                w = weights or [1.0 / len(returns.columns)] * len(returns.columns)
                ret = (returns * w).sum(axis=1)
        else:
            ret = returns

        if action == "stats":
            return self._compute_stats(ret, benchmark, params)
        if action == "returns":
            return self._compute_returns_table(ret, params)
        if action == "drawdown":
            return self._compute_drawdown(ret, params)
        if action == "rolling":
            return self._compute_rolling(ret, benchmark, params)
        if action == "cumulative":
            return self._compute_cumulative(ret, benchmark, params)
        if action == "distribution":
            return self._compute_distribution(ret, params)
        if action == "html_report":
            return self._compute_html_report(ret, benchmark, params)
        # Unreachable
        raise KeyError(action)

    def _compute_stats(
        self,
        ret: pd.Series,
        benchmark: pd.Series | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        rf = float((params or {}).get("risk_free_rate", 0.0))
        qs = self._qs
        metrics: dict[str, Any] = {}
        for name, fn_name in [
            ("sharpe", "sharpe"),
            ("sortino", "sortino"),
            ("volatility", "volatility"),
            ("max_drawdown", "max_drawdown"),
            ("cagr", "cagr"),
            ("calmar", "calmar"),
        ]:
            try:
                fn = getattr(qs.stats, fn_name, None)
                if fn is None:
                    continue
                val = fn(ret, rf=rf) if fn_name in {"sharpe", "sortino"} else fn(ret)
                metrics[name] = _safe_float(val)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qs.stats.%s failed: %s", fn_name, exc)
                metrics[name] = None
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.PERFORMANCE,
            title="Performance Stats",
            metrics=metrics,
            computed_at=datetime.now(UTC),
            notes=f"risk_free_rate={rf}",
        )

    def _compute_returns_table(
        self,
        ret: pd.Series,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        try:
            yearly = self._qs.utils.return_df(ret, "A")
            monthly = self._qs.utils.return_df(ret, "M")
        except Exception as exc:  # noqa: BLE001
            logger.warning("returns table failed: %s", exc)
            yearly = pd.Series(dtype=float)
            monthly = pd.Series(dtype=float)
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.PERFORMANCE,
            title="Returns (monthly/yearly)",
            metrics={
                "yearly_count": len(yearly),
                "monthly_count": len(monthly),
            },
            data=monthly.to_frame("monthly_returns"),
            computed_at=datetime.now(UTC),
        )

    def _compute_drawdown(
        self,
        ret: pd.Series,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        try:
            dd = self._qs.stats.to_drawdown_series(ret)
            periods = self._qs.stats.drawdown_details(dd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("drawdown failed: %s", exc)
            dd = pd.Series(dtype=float)
            periods = pd.DataFrame()
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.RISK,
            title="Drawdown Analysis",
            metrics={
                "max_drawdown_pct": _safe_float(dd.min()) if len(dd) else None,
                "dd_periods_count": len(periods),
            },
            data=dd.to_frame("drawdown"),
            computed_at=datetime.now(UTC),
        )

    def _compute_rolling(
        self,
        ret: pd.Series,
        benchmark: pd.Series | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        window = int((params or {}).get("window", 180))
        try:
            roll_sharpe = self._qs.stats.rolling_sharpe(ret, rolling_period=window)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rolling sharpe failed: %s", exc)
            roll_sharpe = pd.Series(dtype=float)
        try:
            roll_vol = self._qs.stats.rolling_volatility(ret, rolling_period=window)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rolling volatility failed: %s", exc)
            roll_vol = pd.Series(dtype=float)
        if benchmark is not None:
            try:
                greeks = self._qs.stats.rolling_greeks(
                    ret, benchmark, periods=window
                )
                roll_beta = (
                    greeks["beta"]
                    if "beta" in getattr(greeks, "columns", [])
                    else pd.Series(dtype=float)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("rolling beta failed: %s", exc)
                roll_beta = pd.Series(dtype=float)
        else:
            roll_beta = pd.Series(dtype=float)
        data = pd.concat(
            [
                roll_sharpe.rename("rolling_sharpe"),
                roll_vol.rename("rolling_volatility"),
                roll_beta.rename("rolling_beta"),
            ],
            axis=1,
        )
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.PERFORMANCE,
            title=f"Rolling Sharpe / Volatility / Beta ({window}d)",
            metrics={
                "window": window,
                "last_sharpe": _safe_float(roll_sharpe.iloc[-1]) if len(roll_sharpe) else None,
                "last_volatility": _safe_float(roll_vol.iloc[-1]) if len(roll_vol) else None,
                "last_beta": _safe_float(roll_beta.iloc[-1]) if len(roll_beta) else None,
            },
            data=data,
            computed_at=datetime.now(UTC),
        )

    def _compute_cumulative(
        self,
        ret: pd.Series,
        benchmark: pd.Series | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        try:
            strat_cum = (1 + ret).cumprod()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cumulative failed: %s", exc)
            strat_cum = pd.Series(dtype=float)
        if benchmark is not None:
            try:
                bench_cum = (1 + benchmark).cumprod()
            except Exception as exc:  # noqa: BLE001
                logger.warning("benchmark cumulative failed: %s", exc)
                bench_cum = pd.Series(dtype=float)
        else:
            bench_cum = pd.Series(dtype=float)
        data = pd.concat(
            [
                strat_cum.rename("strategy_cum"),
                bench_cum.rename("benchmark_cum"),
            ],
            axis=1,
        )
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.PERFORMANCE,
            title="Cumulative Returns vs Benchmark",
            metrics={
                "total_return": _safe_float(strat_cum.iloc[-1] - 1) if len(strat_cum) else None,
                "benchmark_total_return": _safe_float(bench_cum.iloc[-1] - 1) if len(bench_cum) else None,
            },
            data=data,
            computed_at=datetime.now(UTC),
        )

    def _compute_distribution(
        self,
        ret: pd.Series,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        values = ret.dropna()
        if len(values) == 0:
            data = pd.DataFrame({"bin_center": [], "count": []}, dtype=float)
            metrics: dict[str, Any] = {"periods": 0}
        else:
            try:
                import numpy as np  # noqa: PLC0415

                counts, edges = np.histogram(values.values, bins=30)
                centers = (edges[:-1] + edges[1:]) / 2
                data = pd.DataFrame({"bin_center": centers, "count": counts})
            except Exception as exc:  # noqa: BLE001
                logger.warning("distribution histogram failed: %s", exc)
                data = pd.DataFrame({"bin_center": [], "count": []}, dtype=float)
            metrics = {
                "periods": len(values),
                "mean": _safe_float(values.mean()),
                "std": _safe_float(values.std(ddof=1)),
                "skew": _safe_float(values.skew()),
                "kurtosis": _safe_float(values.kurt()),
            }
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.PERFORMANCE,
            title="Return Distribution",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def _compute_html_report(
        self,
        ret: pd.Series,
        benchmark: pd.Series | None,
        params: dict[str, Any] | None,
    ) -> AnalyticsReport:
        import base64
        import tempfile
        import os
        try:
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as fp:
                path = fp.name
            self._qs.reports.html(
                ret,
                benchmark=benchmark,
                output=path,
                title="QuantStats Report",
            )
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            try:
                os.unlink(path)
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("html report failed: %s", exc)
            b64 = ""
        return AnalyticsReport(
            provider="quantstats",
            kind=AnalyticsReportKind.PERFORMANCE,
            title="HTML Tearsheet",
            metrics={"size_bytes": len(b64)},
            artifacts={"html_base64": b64},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["stats", "returns", "drawdown", "rolling", "cumulative", "distribution", "html_report"]

    def describe_action(self, action: str) -> str:
        return {
            "stats": "50+ performance/risk metrics",
            "returns": "Monthly/yearly returns table",
            "drawdown": "Drawdown periods and series",
            "rolling": "Rolling Sharpe / volatility / beta",
            "cumulative": "Cumulative returns vs benchmark",
            "distribution": "Return distribution histogram",
            "html_report": "Full HTML tearsheet (base64)",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("quantstats", lambda cfg: QuantStatsProvider(cfg))


register()
