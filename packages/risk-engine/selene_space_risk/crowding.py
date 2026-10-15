"""Funding-rate crowding risk rule — detects crowded positioning.

When funding rates sit at an extreme percentile of their recent history,
the market is crowded: too many leveraged positions paying to stay on the
same side. Crowded positioning precedes violent unwind/squeeze moves, so
entries into the crowded side are flagged as advisory risk alerts.

This rule:
  1. Reuses ``FundingRateIndicator`` (selene_space_strategy.indicators.funding_rate)
     to read the funding-rate series, keeping the same ``FUNDING_RATE``
     column convention as the strategy engine.
  2. Computes the percentile rank of the current funding rate within its
     trailing ``lookback`` window.
  3. Alerts when the percentile is extreme — crowded long above
     ``high_percentile``, crowded short below ``low_percentile``.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from selene_space_strategy.indicators.funding_rate import FundingRateIndicator
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _alert(rule_id: str, level: RiskLevel, message: str, **context: Any) -> RiskAlert:
    return RiskAlert(
        rule_id=rule_id,
        level=level,
        message=message,
        context=context,
        timestamp=_now_ms(),
    )


def funding_percentile(history: list[float], value: float) -> float:
    """Percentile rank of ``value`` within ``history``.

    Fraction of history points ``<= value``. Returns 0.0 on empty history.
    """
    if not history:
        return 0.0
    below = sum(1.0 for h in history if h <= value)
    return below / len(history)


class CrowdingRiskRule:
    """Risk rule that flags crowded positioning via extreme funding rates.

    Extreme positive funding percentile = crowded long (over-leveraged longs
    paying to stay in). Extreme negative = crowded short. Both precede
    unwind/squeeze moves, so entries are flagged as advisory alerts.

    Context keys:
        funding_series (list[float]): plain funding-rate history. Preferred —
            deterministic, no DataFrame required.
        funding_data (pd.DataFrame): merged derivatives DataFrame with a
            ``FUNDING_RATE`` column (the format FundingRateIndicator reads).
            Used when no plain series is supplied.
    """

    def __init__(
        self,
        lookback: int = 100,
        high_percentile: float = 0.90,
        low_percentile: float = 0.10,
        *,
        enabled: bool = True,
    ) -> None:
        """Initialize crowding risk rule.

        Args:
            lookback: Trailing window (in bars) for the percentile baseline.
            high_percentile: Percentile at/above which funding is "crowded long".
            low_percentile: Percentile at/below which funding is "crowded short".
            enabled: Whether the rule is active.
        """
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        if not (0.5 < high_percentile <= 1.0):
            raise ValueError("high_percentile must be in (0.5, 1.0]")
        if not (0.0 <= low_percentile < 0.5):
            raise ValueError("low_percentile must be in [0.0, 0.5)")
        self._lookback = lookback
        self._high = high_percentile
        self._low = low_percentile
        self._enabled = enabled
        self._indicator = FundingRateIndicator()

    @property
    def rule_id(self) -> str:
        return "crowding_risk"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self._enabled:
            return None

        series = self._funding_series(context)
        if len(series) < 2:
            return None  # not enough history to establish a baseline

        history = series[-self._lookback - 1 : -1]  # trailing values before current
        current = series[-1]
        if not history:
            return None

        pct = funding_percentile(history, current)
        if pct >= self._high:
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"资金费率处于近 {len(history)} 期第 {pct:.0%} 百分位，拥挤做多（多头拥挤，谨防踩踏回落）",
                symbol=intent.symbol,
                direction="long",
                percentile=pct,
                current_funding=current,
                lookback=len(history),
            )
        if pct <= self._low:
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"资金费率处于近 {len(history)} 期第 {pct:.0%} 百分位，拥挤做空（空头拥挤，谨防逼空反弹）",
                symbol=intent.symbol,
                direction="short",
                percentile=pct,
                current_funding=current,
                lookback=len(history),
            )
        return None

    def _funding_series(self, context: dict[str, Any]) -> list[float]:
        """Extract the funding-rate history from context (list preferred).

        NaN entries are dropped rather than carried into the percentile
        rank: an absent funding column yields an all-NaN series, and a NaN
        slipping through would make every comparison False and pin the
        percentile at 0.0 — a fabricated "crowded short" reading.
        """
        plain = context.get("funding_series")
        if plain is not None:
            return [float(v) for v in plain if v == v]
        data = context.get("funding_data")
        if isinstance(data, pd.DataFrame):
            try:
                series = self._indicator.calculate(data)
                return [float(v) for v in series if v == v]
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("funding_data extraction failed: %s", exc)
                return []
        return []
