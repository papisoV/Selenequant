"""Portfolio correlation risk rule — detects concentrated directional exposure.

When multiple positions are highly correlated (e.g. BTC, ETH, SOL all moving
together), individual position limits understate the true portfolio risk.
Five correlated positions at 20% each = 100% same-direction exposure, but
per-symbol rules think it's only 20% each.

This rule computes:
  1. A rolling correlation matrix from price history
  2. Portfolio effective exposure: sum of positions weighted by their
     correlation cluster's collective size
  3. Alerts when effective same-direction exposure exceeds threshold
"""

from __future__ import annotations

import math
from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel


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


def pearson_correlation(a: list[float], b: list[float]) -> float | None:
    """Compute Pearson correlation coefficient between two series.

    Returns None if insufficient data or zero variance.
    """
    n = min(len(a), len(b))
    if n < 3:
        return None
    a_slice = a[-n:]
    b_slice = b[-n:]
    mean_a = sum(a_slice) / n
    mean_b = sum(b_slice) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a_slice, b_slice)) / n
    var_a = sum((x - mean_a) ** 2 for x in a_slice) / n
    var_b = sum((y - mean_b) ** 2 for y in b_slice) / n
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / (math.sqrt(var_a) * math.sqrt(var_b))


def compute_returns(prices: list[float]) -> list[float]:
    """Convert price series to percentage returns."""
    if len(prices) < 2:
        return []
    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] != 0:
            returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        else:
            returns.append(0.0)
    return returns


class CorrelationRiskRule:
    """Risk rule that detects concentrated portfolio exposure via correlation.

    Groups positions into correlation clusters and calculates effective
    same-direction exposure. If effective exposure exceeds the threshold,
    the rule alerts — the portfolio is effectively a single large bet.

    Context keys:
        total_equity (float): Total account equity.
        positions (dict[str, float]): symbol → current position value (USDT notional).
        price_history (dict[str, list[float]]): symbol → recent close prices
            for rolling correlation calculation.
    """

    def __init__(
        self,
        max_effective_exposure: float = 0.60,
        correlation_threshold: float = 0.70,
        min_prices: int = 20,
        *,
        enabled: bool = True,
    ) -> None:
        """Initialize correlation risk rule.

        Args:
            max_effective_exposure: Max fraction of equity in correlated positions.
                Default 0.60 = 60% effective exposure limit.
            correlation_threshold: Pearson |r| above which two symbols are
                considered correlated. Default 0.70.
            min_prices: Minimum price data points needed for correlation calc.
            enabled: Whether the rule is active.
        """
        if max_effective_exposure < 0 or max_effective_exposure > 1:
            raise ValueError("max_effective_exposure must be in [0, 1]")
        if correlation_threshold < 0 or correlation_threshold > 1:
            raise ValueError("correlation_threshold must be in [0, 1]")
        self._max_exposure = max_effective_exposure
        self._corr_threshold = correlation_threshold
        self._min_prices = min_prices
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "correlation_risk"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self._enabled:
            return None

        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        positions: dict[str, float] = context.get("positions", {})
        price_history: dict[str, list[float]] = context.get("price_history", {})

        # Add the new intent's value to the position
        intent_value = intent.quantity * (intent.price or 0.0)
        current_value = positions.get(intent.symbol, 0.0)
        updated_positions = dict(positions)
        updated_positions[intent.symbol] = current_value + intent_value

        # Build correlation clusters
        clusters = self._find_correlation_clusters(updated_positions, price_history)

        # Calculate effective exposure per cluster
        for cluster_symbols, cluster_value in clusters:
            exposure_pct = cluster_value / total_equity
            if exposure_pct > self._max_exposure:
                symbols_str = ", ".join(sorted(cluster_symbols))
                return _alert(
                    self.rule_id, RiskLevel.RESTRICTED,
                    f"相关持仓 [{symbols_str}] 有效暴露 {exposure_pct:.1%}，超过上限 {self._max_exposure:.0%}（{len(cluster_symbols)}个高度相关标的）",
                    cluster_symbols=list(sorted(cluster_symbols)),
                    effective_exposure=exposure_pct,
                    cluster_value=cluster_value,
                    max_exposure=self._max_exposure,
                )

        return None

    def _find_correlation_clusters(
        self,
        positions: dict[str, float],
        price_history: dict[str, list[float]],
    ) -> list[tuple[set[str], float]]:
        """Group positions into correlation clusters using union-find.

        Returns list of (symbols_in_cluster, total_notional_value).
        """
        active_symbols = [s for s, v in positions.items() if v > 0]
        if len(active_symbols) < 2:
            # Single position or no positions — no correlation risk
            if active_symbols:
                return [(set(active_symbols), positions[active_symbols[0]])]
            return []

        # Union-Find for clustering
        parent: dict[str, str] = {s: s for s in active_symbols}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Check pairwise correlations
        for i, sym_a in enumerate(active_symbols):
            prices_a = price_history.get(sym_a, [])
            if len(prices_a) < self._min_prices:
                continue
            for sym_b in active_symbols[i + 1:]:
                prices_b = price_history.get(sym_b, [])
                if len(prices_b) < self._min_prices:
                    continue

                # Use returns for correlation (more stationary than prices)
                returns_a = compute_returns(prices_a)
                returns_b = compute_returns(prices_b)
                corr = pearson_correlation(returns_a, returns_b)

                if corr is not None and abs(corr) > self._corr_threshold:
                    union(sym_a, sym_b)

        # Group by root
        clusters: dict[str, set[str]] = {}
        for sym in active_symbols:
            root = find(sym)
            clusters.setdefault(root, set()).add(sym)

        # Calculate cluster values
        result: list[tuple[set[str], float]] = []
        for symbols in clusters.values():
            total_value = sum(positions.get(s, 0.0) for s in symbols)
            result.append((symbols, total_value))

        return result
