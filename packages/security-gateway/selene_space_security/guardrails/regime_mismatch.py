"""Strategy-vs-market-regime compatibility guards."""

from __future__ import annotations

from dataclasses import dataclass

from selene_space_types import MarketRegime, RiskLevel, StrategyType

_WEAK_TREND = 0.3
_STRONG_TREND = 0.7
_HIGH_VOLATILITY = 0.8
_DEFAULT_MAX_VOLATILITY = 0.7

_ALL_REGIMES = frozenset(MarketRegime)

STRATEGY_REGIME_COMPAT: dict[StrategyType, set[MarketRegime]] = {
    StrategyType.SMA_CROSS: {MarketRegime.TRENDING},
    StrategyType.RSI_REVERSAL: {MarketRegime.RANGING},
    StrategyType.MACD: {MarketRegime.TRENDING, MarketRegime.VOLATILE},
    StrategyType.SENTIMENT: {MarketRegime.RANGING, MarketRegime.VOLATILE},
    StrategyType.CUSTOM: set(_ALL_REGIMES),
    StrategyType.ICT: {MarketRegime.TRENDING, MarketRegime.VOLATILE},
    StrategyType.ICT_FVG: {MarketRegime.TRENDING, MarketRegime.VOLATILE},
}


@dataclass(frozen=True)
class RegimeMismatchAlert:
    strategy_type: StrategyType
    current_regime: MarketRegime
    expected_regimes: frozenset[MarketRegime]
    severity: RiskLevel
    message: str


class RegimeMismatchDetector:
    def __init__(
        self,
        custom_compat: dict[StrategyType, set[MarketRegime]] | None = None,
    ) -> None:
        source = custom_compat if custom_compat is not None else STRATEGY_REGIME_COMPAT
        self._compat: dict[StrategyType, set[MarketRegime]] = {
            strategy: set(regimes) for strategy, regimes in source.items()
        }

    def check_mismatch(
        self,
        strategy_type: StrategyType,
        current_regime: MarketRegime,
        *,
        strategy_name: str | None = None,
        trend_strength: float | None = None,
        volatility: float | None = None,
    ) -> RegimeMismatchAlert | None:
        expected = self._compat.get(strategy_type, set())
        if current_regime in expected:
            return None
        severity = self._grade_severity(expected, trend_strength, volatility)
        label = strategy_name if strategy_name is not None else strategy_type.value
        expected_summary = (
            "、".join(sorted(regime.value for regime in expected)) or "无"
        )
        message = (
            f"策略 “{label}”（{strategy_type.value}）与当前 "
            f"{current_regime.value} 行情制度不匹配；适配行情：{expected_summary}。"
        )
        return RegimeMismatchAlert(
            strategy_type=strategy_type,
            current_regime=current_regime,
            expected_regimes=frozenset(expected),
            severity=severity,
            message=message,
        )

    def _grade_severity(
        self,
        expected: set[MarketRegime],
        trend_strength: float | None,
        volatility: float | None,
    ) -> RiskLevel:
        severity = RiskLevel.RESTRICTED
        favours_trend = MarketRegime.TRENDING in expected
        if trend_strength is not None:
            if favours_trend and trend_strength <= _WEAK_TREND:
                severity = RiskLevel.DANGEROUS
            elif not favours_trend and trend_strength >= _STRONG_TREND:
                severity = RiskLevel.DANGEROUS
        if volatility is not None and volatility >= _HIGH_VOLATILITY:
            severity = RiskLevel.DANGEROUS
        return severity

    def check_volatility_mismatch(
        self,
        strategy_type: StrategyType,
        volatility: float,
        max_volatility: float | None = None,
    ) -> RegimeMismatchAlert | None:
        expected = self._compat.get(strategy_type, set())
        threshold = (
            max_volatility if max_volatility is not None else _DEFAULT_MAX_VOLATILITY
        )
        if max_volatility is None and MarketRegime.VOLATILE in expected:
            return None
        if volatility < threshold:
            return None
        message = (
            f"{strategy_type.value} 对高波动环境耐受不足：当前 "
            f"volatility={volatility:.2f} 超过阈值 {threshold:.2f}，建议降仓或换策略。"
        )
        return RegimeMismatchAlert(
            strategy_type=strategy_type,
            current_regime=MarketRegime.VOLATILE,
            expected_regimes=frozenset(expected),
            severity=RiskLevel.DANGEROUS,
            message=message,
        )
