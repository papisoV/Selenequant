"""Backtest overfitting detection — DSR, PBO, haircut Sharpe, purged CV."""

from .deflated_sharpe import (
    DSRResult,
    annualised_to_per_period,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)
from .haircut import HaircutResult, haircut_sharpe
from .pbo_cscv import PBOResult, probability_of_backtest_overfitting
from .purged_kfold import PurgedKFold, cross_val_score_purged
from .report import OverfitReport, build_overfit_report, render_text

__all__ = [
    "DSRResult",
    "HaircutResult",
    "OverfitReport",
    "PBOResult",
    "PurgedKFold",
    "annualised_to_per_period",
    "build_overfit_report",
    "cross_val_score_purged",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "haircut_sharpe",
    "minimum_track_record_length",
    "probabilistic_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "render_text",
    "sharpe_ratio",
]
