"""backtest_viz — clean-room visualization utilities for alpha mining results.

See README.md for the public API design.
"""

from selene_space_alpha.backtest_viz.chart import (
    plot_formula_tree,
    plot_training_curve,
)
from selene_space_alpha.backtest_viz.engine import VizEngine
from selene_space_alpha.backtest_viz.report import generate_report

__all__ = [
    "VizEngine",
    "generate_report",
    "plot_formula_tree",
    "plot_training_curve",
]
