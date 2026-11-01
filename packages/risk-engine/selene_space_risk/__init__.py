"""SeleneQuant risk engine package.

Pre-trade defense line for the trading stack. :class:`RiskEngine` evaluates
every enabled rule against a :class:`~selene_space_types.agent.TradeIntent`
and merges their findings into one :class:`~selene_space_types.risk.RiskDecision`;
only CRITICAL-level alerts hard-block a trade (and can trip the kill switch /
flatten hook). Everything else is advisory surfaced to the UI.

Public API
----------
- Core: ``RiskEngine``, ``RiskRuleProtocol``
- Basic rules: ``MaxPositionRule``, ``MaxSingleRiskRule``, ``DailyLossLimitRule``,
  ``SymbolBlacklistRule``, ``CooldownRule``, factory ``create_default_rules``
- Advanced rules: ``ATRDynamicStopRule``, ``FloatingLossBlockRule``,
  ``DrawdownCircuitBreakerRule``, ``OnePercentRiskRule``,
  ``KellyPositionSizingRule``, ``VolatilityScaledKellyRule``,
  ``IOCLoopCloseRule``, ``DailyLossFuseRule``, factory ``create_advanced_rules``
- DEX rules: ``TrailingStopRule``, ``PartialTakeProfitRule``,
  ``AISignalExitRule``, ``HoneypotDetectionRule``, ``LiquidityFilterRule``,
  factory ``create_dex_rules``
- Market rules: ``PDTRule``, ``T1SettlementRule``, ``PriceLimitRule``
- Portfolio analytics: ``CorrelationRiskRule``, ``pearson_correlation``,
  ``compute_returns``, ``CrowdingRiskRule``, ``funding_percentile``,
  ``MarketBreadthFilter``, ``compute_market_breadth``, ``market_breadth_counts``
- Monitoring / safety: ``ContinuousRiskMonitor``, ``KillSwitch``
- Self-trade guard: ``ActiveOrder``, ``OrderTracker``, ``SelfTradePreventionRule``
- Portfolio mutex: ``PortfolioMutex``, ``SymbolLock``, ``PortfolioMutexRule``
- Intent validators: ``validate``, ``ValidationResult``
- Type re-exports: ``RiskLevel``, ``RiskAlert``, ``RiskDecision``, ``RiskAlertConfig``
"""

from __future__ import annotations

from selene_space_risk.advanced_rules import (
    ATRDynamicStopRule,
    DailyLossFuseRule,
    DrawdownCircuitBreakerRule,
    FloatingLossBlockRule,
    IOCLoopCloseRule,
    KellyPositionSizingRule,
    OnePercentRiskRule,
    VolatilityScaledKellyRule,
    create_advanced_rules,
)
from selene_space_risk.correlation import (
    CorrelationRiskRule,
    compute_returns,
    pearson_correlation,
)
from selene_space_risk.crowding import CrowdingRiskRule, funding_percentile
from selene_space_risk.dex_rules import (
    AISignalExitRule,
    HoneypotDetectionRule,
    LiquidityFilterRule,
    PartialTakeProfitRule,
    TrailingStopRule,
    create_dex_rules,
)
from selene_space_risk.engine import RiskEngine, RiskRuleProtocol
from selene_space_risk.kill_switch import KillSwitch
from selene_space_risk.market_breadth import (
    MarketBreadthFilter,
    compute_market_breadth,
    market_breadth_counts,
)
from selene_space_risk.market_rules import PDTRule, PriceLimitRule, T1SettlementRule
from selene_space_risk.monitor import ContinuousRiskMonitor
from selene_space_risk.portfolio_mutex import PortfolioMutex, SymbolLock
from selene_space_risk.portfolio_mutex_rule import PortfolioMutexRule
from selene_space_risk.rules import (
    CooldownRule,
    DailyLossLimitRule,
    MaxPositionRule,
    MaxSingleRiskRule,
    SymbolBlacklistRule,
    create_default_rules,
)
from selene_space_risk.self_trade import (
    ActiveOrder,
    OrderTracker,
    SelfTradePreventionRule,
)
from selene_space_risk.stop_proximity import (
    StaticStopPriceProvider,
    StopPriceProvider,
    audit_stop_proximity,
)
from selene_space_risk.validators import ValidationResult, validate
from selene_space_types.risk import RiskAlert, RiskAlertConfig, RiskDecision, RiskLevel

__all__ = [
    "RiskEngine",
    "RiskRuleProtocol",
    "RiskLevel",
    "RiskAlert",
    "RiskDecision",
    "RiskAlertConfig",
    "MaxPositionRule",
    "MaxSingleRiskRule",
    "DailyLossLimitRule",
    "SymbolBlacklistRule",
    "CooldownRule",
    "create_default_rules",
    "TrailingStopRule",
    "PartialTakeProfitRule",
    "AISignalExitRule",
    "HoneypotDetectionRule",
    "LiquidityFilterRule",
    "create_dex_rules",
    "ATRDynamicStopRule",
    "FloatingLossBlockRule",
    "DrawdownCircuitBreakerRule",
    "OnePercentRiskRule",
    "KellyPositionSizingRule",
    "VolatilityScaledKellyRule",
    "IOCLoopCloseRule",
    "DailyLossFuseRule",
    "create_advanced_rules",
    "ActiveOrder",
    "OrderTracker",
    "SelfTradePreventionRule",
    "PortfolioMutex",
    "SymbolLock",
    "PortfolioMutexRule",
    "CorrelationRiskRule",
    "pearson_correlation",
    "compute_returns",
    "PDTRule",
    "T1SettlementRule",
    "PriceLimitRule",
    "ContinuousRiskMonitor",
    "validate",
    "ValidationResult",
    "KillSwitch",
    "CrowdingRiskRule",
    "funding_percentile",
    "MarketBreadthFilter",
    "compute_market_breadth",
    "market_breadth_counts",
    "StopPriceProvider",
    "StaticStopPriceProvider",
    "audit_stop_proximity",
]
