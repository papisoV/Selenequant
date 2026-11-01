"""selene_space_strategy — config → validation → compilation → runtime signals.

Public surface of the strategy engine package. Everything re-exported here
is also importable from its home submodule; the facade exists so downstream
code can rely on stable top-level names.

Pipeline orientation::

    YAML/dict ──models──▶ EngineStrategyConfig
              ──validator─▶ ValidationResult
              ──compiler──▶ CompiledStrategy
              ──runtime───▶ Signal

Peripheral layers (templates/factory presets, optimizer, ensemble fusion,
multi-timeframe helpers) wrap that spine.
"""

# -- config spine ------------------------------------------------------------
from selene_space_strategy.compiler import CompiledStrategy, compile
from selene_space_strategy.features import FeatureVector
from selene_space_strategy.models import EngineStrategyConfig, load_strategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_strategy.validator import (
    Recovery,
    ValidationIssue,
    ValidationResult,
    validate,
)

# -- lifecycle ------------------------------------------------------------------
from selene_space_strategy.lifecycle import (
    BacktestReadyStrategy,
    LifecycleError,
    StrategyDraft,
    StrategyState,
    ValidatedStrategy,
    draft_from_config,
    submit_for_backtest,
    validate_draft,
)

# -- template method pattern (recommended authoring entry point) -----------------
from selene_space_strategy.template import (
    BacktestResult,
    BacktestStrategyTemplate,
    SignalType,
)

# -- indicator mixin ----------------------------------------------------------
from selene_space_strategy.indicators.mixin import (
    IndicatorMixIn,
    IndicatorWrapper,
    MACDWrapper,
)

# -- parameter optimization ------------------------------------------------------
from selene_space_strategy.optimizer import (
    OptimizationResult,
    StrategyOptimizer,
    WalkForwardResult,
    optimize_strategy,
)

# -- multi-strategy fusion --------------------------------------------------------
from selene_space_strategy.fusion.ensemble import (
    DynamicThresholdConfig,
    EnsembleConfig,
    EnsembleSignal,
    FusionMethod,
    MarketRegime,
    StrategyEnsemble,
    detect_market_regime,
)

# -- factory / preset catalog ---------------------------------------------------
from selene_space_strategy.factory import (
    SIGNAL_BUY,
    SIGNAL_HOLD,
    SIGNAL_SELL,
    StrategyCategory,
    StrategyFactory,
    StrategyMetadata,
    build_signals,
    create_strategy,
    generate_breakout_signals,
    generate_crossover_signals,
    generate_mean_reversion_signals,
    generate_threshold_signals,
    get_fusion_strategy_info,
    get_fusion_strategies,
    list_available_strategies,
)

# -- statistical arbitrage presets ---------------------------------------------
from selene_space_strategy.templates.presets.statistical import (
    HurstReversionStrategy,
    PairsTradingStrategy,
)

# -- multi-timeframe tooling -----------------------------------------------------
from selene_space_strategy.mtf import (
    KlineResampler,
    MTFConfluence,
    MTFSignal,
    TimeframeRole,
)
from selene_space_strategy.mtf_runtime import MTFRuntimeAggregator

# Public name registry, assembled from per-area groups.
_CONFIG_AND_RUNTIME = (
    "EngineStrategyConfig",
    "load_strategy",
    "FeatureVector",
    "ValidationIssue",
    "ValidationResult",
    "validate",
    "Recovery",
    "CompiledStrategy",
    "compile",
    "StrategyRuntime",
)

_LIFECYCLE_NAMES = (
    "BacktestReadyStrategy",
    "LifecycleError",
    "StrategyDraft",
    "StrategyState",
    "ValidatedStrategy",
    "draft_from_config",
    "submit_for_backtest",
    "validate_draft",
)

_TEMPLATE_NAMES = (
    "BacktestStrategyTemplate",
    "BacktestResult",
    "SignalType",
)

_OPTIMIZER_NAMES = (
    "StrategyOptimizer",
    "OptimizationResult",
    "WalkForwardResult",
    "optimize_strategy",
)

_MIXIN_NAMES = (
    "IndicatorMixIn",
    "IndicatorWrapper",
    "MACDWrapper",
)

_FUSION_NAMES = (
    "DynamicThresholdConfig",
    "EnsembleConfig",
    "EnsembleSignal",
    "FusionMethod",
    "MarketRegime",
    "StrategyEnsemble",
    "detect_market_regime",
)

_FACTORY_NAMES = (
    "StrategyFactory",
    "StrategyCategory",
    "StrategyMetadata",
    "create_strategy",
    "build_signals",
    "list_available_strategies",
    "generate_crossover_signals",
    "generate_threshold_signals",
    "generate_breakout_signals",
    "generate_mean_reversion_signals",
    "SIGNAL_BUY",
    "SIGNAL_SELL",
    "SIGNAL_HOLD",
    "get_fusion_strategies",
    "get_fusion_strategy_info",
)

_STATARB_NAMES = (
    "PairsTradingStrategy",
    "HurstReversionStrategy",
)

_MTF_NAMES = (
    "MTFConfluence",
    "KlineResampler",
    "TimeframeRole",
    "MTFSignal",
    "MTFRuntimeAggregator",
)

__all__ = [
    *_CONFIG_AND_RUNTIME,
    *_TEMPLATE_NAMES,
    *_FACTORY_NAMES,
    *_OPTIMIZER_NAMES,
    *_MIXIN_NAMES,
    *_FUSION_NAMES,
    *_LIFECYCLE_NAMES,
    *_STATARB_NAMES,
    *_MTF_NAMES,
]
