"""Multi-asset backtest engine.

Supports backtesting a portfolio across multiple symbols simultaneously,
with configurable allocation models (equal-weight, fixed-weight).
"""

from selene_space_backtest.multi_asset.allocation_model import (
    AllocationModel,
    EqualWeightAllocation,
    FixedWeightAllocation,
)
from selene_space_backtest.multi_asset.multi_asset_engine import (
    MultiAssetBacktestConfig,
    MultiAssetEngine,
)
from selene_space_backtest.multi_asset.portfolio_broker import MultiAssetPortfolio

__all__ = [
    "AllocationModel",
    "EqualWeightAllocation",
    "FixedWeightAllocation",
    "MultiAssetBacktestConfig",
    "MultiAssetEngine",
    "MultiAssetPortfolio",
]
