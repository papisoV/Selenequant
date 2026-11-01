"""Public surface of the indicator library.

Importing this package eagerly exposes every built-in indicator class,
the registry machinery, and the fluent mixin layer. The exported symbol
set below is frozen interface: downstream packages (alpha-core,
market-analysis, local-api) import these names directly, so additions
are safe but removals or renames are breaking changes.
"""

from __future__ import annotations

# --- abstract contract -------------------------------------------------
from selene_space_strategy.indicators.base import Indicator

# --- core oscillators and overlays -------------------------------------
from selene_space_strategy.indicators.adx import ADX, ADXResult
from selene_space_strategy.indicators.atr import ATR
from selene_space_strategy.indicators.bbands import BollingerBands
from selene_space_strategy.indicators.chop import ChoppinessIndex
from selene_space_strategy.indicators.close import Close
from selene_space_strategy.indicators.efficiency_ratio import EfficiencyRatio
from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.kdj import KDJ, KDJResult
from selene_space_strategy.indicators.macd import MACD, MACDResult
from selene_space_strategy.indicators.ppo import PPO, PPOResult
from selene_space_strategy.indicators.rsi import RSI
from selene_space_strategy.indicators.sma import SMA
from selene_space_strategy.indicators.stoch import Stochastic, StochasticResult

# --- statistical mean-reversion diagnostics ----------------------------
from selene_space_strategy.indicators.statistical import (
    HalfLifeIndicator,
    HurstExponent,
    ZScoreIndicator,
)

# --- volume flow family --------------------------------------------------
from selene_space_strategy.indicators.volume import (
    ChaikinMoneyFlow,
    ForceIndex,
    MoneyFlowIndex,
    OnBalanceVolume,
)

# --- structure / microstructure / derivatives ----------------------------
from selene_space_strategy.indicators.cpr import CPR, CPRLevels
from selene_space_strategy.indicators.divergence import Divergence
from selene_space_strategy.indicators.funding_rate import FundingRateIndicator, FundingRateZScore
from selene_space_strategy.indicators.volume_cluster import VolumeCluster, VolumeClusterZone

# --- registry machinery ----------------------------------------------------
from selene_space_strategy.indicators.registry import (
    IndicatorNotFoundError,
    IndicatorRegistry,
    indicator_registry,
)

# --- fluent mixin layer -----------------------------------------------------
from selene_space_strategy.indicators.mixin import IndicatorMixIn, IndicatorWrapper, MACDWrapper

# Historic alias kept for call sites predating the Stochastic rename.
Stoch = Stochastic
StochResult = StochasticResult

__all__ = [
    # contract
    "Indicator",
    # core indicators
    "ADX", "ADXResult", "ATR", "ChoppinessIndex", "Close",
    "EfficiencyRatio", "KDJ", "KDJResult", "MACD", "MACDResult",
    "PPO", "PPOResult",
    # stochastic family (+ legacy aliases)
    "Stochastic", "StochasticResult", "Stoch", "StochResult",
    # statistical diagnostics
    "HalfLifeIndicator", "HurstExponent", "ZScoreIndicator",
    # volume flow
    "ChaikinMoneyFlow", "ForceIndex", "MoneyFlowIndex", "OnBalanceVolume",
    # structure / microstructure / derivatives
    "CPR", "CPRLevels", "Divergence",
    "FundingRateIndicator", "FundingRateZScore",
    "VolumeCluster", "VolumeClusterZone",
    # registry
    "IndicatorNotFoundError", "IndicatorRegistry", "indicator_registry",
    # fluent mixin
    "IndicatorMixIn", "IndicatorWrapper", "MACDWrapper",
]
