"""Weight-drift quantile backtest and IC diagnostic panel (pandas, no torch).

Ported from AlphaPurify's ``calc_stats_for_period`` (weight drift) and
``calc_stats_for_horizon`` IC section; see :mod:`selene_space_factor_research`.
"""

from .cost_models import AShareCostModel, CostModel, validate_tax_rate_direction
from .funding_source import FundingRateSource, build_funding_cost_model
from .ic_panel import ICPanel, ICPanelResult
from .parallel_runner import ParallelBacktestRunner, ParallelGridResult
from .weight_drift_backtest import WeightDriftBacktest, WeightDriftConfig
from .weight_drift_result import WeightDriftResult

__all__ = [
    "AShareCostModel",
    "CostModel",
    "validate_tax_rate_direction",
    "FundingRateSource",
    "build_funding_cost_model",
    "ICPanel",
    "ICPanelResult",
    "ParallelBacktestRunner",
    "ParallelGridResult",
    "WeightDriftBacktest",
    "WeightDriftConfig",
    "WeightDriftResult",
]
