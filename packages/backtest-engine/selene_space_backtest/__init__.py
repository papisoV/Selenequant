"""Public surface of the backtesting package.

Importing the package costs nothing up front: every exported name
resolves through a PEP 562 hook on first touch, pulling its defining
submodule at that moment and memoizing the result into module globals.
Static tooling sees the full surface through ``__all__``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from selene_space_backtest import (
        broker,
        commissions,
        engine,
        etf_rotation,
        metrics,
        overfit,
        paper_benchmark,
        portfolio,
        protocols,
        reports,
        slippage,
    )

# Inverted view of the export table: which names live in each module.
# Kept as one entry per source module so adding an export is a single
# line in exactly one place.
_LAZY_MODULES: dict[str, tuple[str, ...]] = {
    "broker": ("SimulatedBroker",),
    "commissions": (
        "PercentageCommission",
        "NoCommission",
        "MakerTakerCommission",
        "CommissionModel",
    ),
    "engine": ("BacktestError", "BacktestEngine", "BacktestConfig"),
    "etf_rotation": (
        "RotationParams",
        "Trade",
        "BacktestResult",
        "run_backtest",
        "momentum_scores",
        "select_holdings",
        "holdings_to_weights",
        "rebalance_dates",
        "is_rebalance_day",
        "parameter_sweep",
        "latest_ranking",
        "momentum_metrics",
        "annual_returns",
    ),
    "metrics": ("compute_metrics", "compute_ic_summary", "compute_ic"),
    "robustness": (
        "compute_robustness",
        "permutation_test",
        "bootstrap_sharpe_ci",
        "equity_curve_percentiles",
    ),
    "overfit": (
        "render_text",
        "probability_of_backtest_overfitting",
        "haircut_sharpe",
        "deflated_sharpe_ratio",
        "build_overfit_report",
        "PurgedKFold",
        "PBOResult",
        "OverfitReport",
        "HaircutResult",
        "DSRResult",
    ),
    "paper_benchmark": (
        "render_benchmark_report",
        "get_by_selene",
        "get_benchmark",
        "compare_to_paper",
        "all_benchmarks",
        "PaperBenchmark",
        "SELENE_ALIASES",
        "PAPER_BENCHMARKS",
        "BenchmarkComparison",
    ),
    "portfolio": ("Portfolio",),
    "protocols": ("StrategyRunner",),
    "reports": ("to_json", "to_html_report", "to_dict"),
    "slippage": (
        "SlippageModel",
        "RealisticSlippage",
        "NoSlippage",
        "FixedSlippage",
    ),
}

_EXPORT_SOURCES = {
    name: f"selene_space_backtest.{module}"
    for module, names in _LAZY_MODULES.items()
    for name in names
}

__all__ = sorted(_EXPORT_SOURCES)


def __getattr__(name: str):  # noqa: ANN202 - PEP 562 hook has no useful return type
    if name not in _EXPORT_SOURCES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    resolved = getattr(__import__(_EXPORT_SOURCES[name], fromlist=[name]), name)
    globals()[name] = resolved  # memoize so repeat lookups skip the hook
    return resolved


def __dir__() -> list[str]:
    """Union of live globals with everything the lazy table promises."""
    return sorted(set(globals()) | set(_EXPORT_SOURCES))
