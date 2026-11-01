"""StrategyRecommender — Regime-aware strategy combination recommender.

Recommends optimal strategy combinations based on:
1. Current market regime detection
2. Historical tuning results (Sharpe, win rate, profit factor)
3. Signal correlation matrix for diversification
4. Strategy category diversity weighting

Output: 3-5 strategy combination with weights and expected performance range.

Usage:
    from selene_space_strategy.fusion.recommender import StrategyRecommender

    recommender = StrategyRecommender()
    combos = recommender.recommend(
        data=kline_events,
        tuning_results=tuning_json,
        top_k=3,
    )
    for combo in combos:
        print(combo)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from selene_space_strategy.fusion.ensemble import (
    MarketRegime,
    _DEFAULT_REGIME_WEIGHTS,
    detect_market_regime,
)
from selene_space_types.market import KlineEvent


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass
class StrategyProfile:
    """Performance profile of a single strategy from tuning results."""

    strategy_id: str
    strategy_name: str = ""
    category: str = ""
    interval: str = "1d"
    sharpe: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def quality_score(self) -> float:
        """Overall quality metric combining multiple factors."""
        if self.total_trades < 1:
            return 0.0
        # Penalize low trade count (unreliable stats)
        trade_penalty = min(self.total_trades / 10, 1.0)
        return (
            0.4 * min(max(self.sharpe, 0), 2.0) / 2.0
            + 0.3 * min(max(self.win_rate, 0), 1.0)
            + 0.2 * min(max(self.profit_factor, 0), 5.0) / 5.0
            + 0.1 * trade_penalty
        )


@dataclass
class RecommendedCombination:
    """A recommended strategy combination with weights and expected performance."""

    strategies: list[StrategyProfile] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    regime: MarketRegime = MarketRegime.SIDEWAYS
    diversity_score: float = 0.0
    avg_correlation: float = 0.0
    expected_sharpe_range: tuple[float, float] = (0.0, 0.0)
    expected_mdd_range: tuple[float, float] = (0.0, 0.0)

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Regime: {self.regime.value}",
            f"Diversity: {self.diversity_score:.2f}, Avg Correlation: {self.avg_correlation:.2f}",
            f"Expected Sharpe: [{self.expected_sharpe_range[0]:.2f}, {self.expected_sharpe_range[1]:.2f}]",
            f"Expected MDD: [{self.expected_mdd_range[0]:.2%}, {self.expected_mdd_range[1]:.2%}]",
            "Strategies:",
        ]
        for p in self.strategies:
            w = self.weights.get(p.strategy_id, 1.0)
            lines.append(
                f"  {p.strategy_id} (w={w:.2f}): Sharpe={p.sharpe:.2f}, "
                f"WR={p.win_rate:.1%}, PF={p.profit_factor:.2f}, Cat={p.category}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Signal Correlation Calculator
# ---------------------------------------------------------------------------


def compute_signal_correlation(
    strategy_signals: dict[str, list[int]],
) -> dict[str, dict[str, float]]:
    """Compute pairwise signal correlation between strategies.

    Args:
        strategy_signals: Mapping of strategy_id to signal series.
            Signals encoded as: 1=BUY, -1=SELL, 0=HOLD.

    Returns:
        Nested dict of pairwise correlation coefficients.
    """
    sids = list(strategy_signals.keys())
    n = len(sids)
    corr: dict[str, dict[str, float]] = {s: {} for s in sids}

    for i in range(n):
        for j in range(i, n):
            a = np.array(strategy_signals[sids[i]], dtype=float)
            b = np.array(strategy_signals[sids[j]], dtype=float)
            min_len = min(len(a), len(b))
            if min_len < 2:
                corr[sids[i]][sids[j]] = 0.0
                corr[sids[j]][sids[i]] = 0.0
                continue
            a, b = a[:min_len], b[:min_len]
            std_a, std_b = np.std(a), np.std(b)
            if std_a > 0 and std_b > 0:
                c = float(np.corrcoef(a, b)[0, 1])
            else:
                c = 0.0
            corr[sids[i]][sids[j]] = c
            corr[sids[j]][sids[i]] = c

    return corr


# ---------------------------------------------------------------------------
# StrategyRecommender
# ---------------------------------------------------------------------------


# Category diversity mapping — how different two categories are
_CATEGORY_DISTANCE: dict[str, dict[str, float]] = {
    "trend": {"trend": 0.0, "momentum": 0.3, "reversion": 0.8, "breakout": 0.4, "timing": 0.7, "volatility": 0.5},
    "momentum": {"trend": 0.3, "momentum": 0.0, "reversion": 0.6, "breakout": 0.3, "timing": 0.5, "volatility": 0.4},
    "reversion": {"trend": 0.8, "momentum": 0.6, "reversion": 0.0, "breakout": 0.5, "timing": 0.4, "volatility": 0.3},
    "breakout": {"trend": 0.4, "momentum": 0.3, "reversion": 0.5, "breakout": 0.0, "timing": 0.6, "volatility": 0.4},
    "timing": {"trend": 0.7, "momentum": 0.5, "reversion": 0.4, "breakout": 0.6, "timing": 0.0, "volatility": 0.5},
    "volatility": {"trend": 0.5, "momentum": 0.4, "reversion": 0.3, "breakout": 0.4, "timing": 0.5, "volatility": 0.0},
}

# Strategies that manage their own positions (not suitable for signal-level fusion)
_SELF_MANAGED_STRATEGIES = {"dca_grid", "calendar_effect", "pyramid_ma"}


class StrategyRecommender:
    """Recommends optimal strategy combinations based on regime and diversity.

    Pipeline:
    1. Detect current market regime from price data
    2. Filter strategies by quality threshold and regime fitness
    3. Compute signal correlation matrix for diversification
    4. Select top-K combinations maximizing diversity score
    5. Assign regime-aware weights and estimate performance range
    """

    def __init__(
        self,
        min_sharpe: float = 0.0,
        min_trades: int = 1,
        max_correlation: float = 0.7,
        combo_size: tuple[int, int] = (3, 5),
    ) -> None:
        """Initialize recommender.

        Args:
            min_sharpe: Minimum Sharpe ratio to consider a strategy.
            min_trades: Minimum trade count for reliable statistics.
            max_correlation: Maximum allowed correlation between strategies in a combo.
            combo_size: (min, max) number of strategies per combination.
        """
        self.min_sharpe = min_sharpe
        self.min_trades = min_trades
        self.max_correlation = max_correlation
        self.combo_size = combo_size

    def recommend(
        self,
        data: list[KlineEvent],
        tuning_results: list[dict[str, Any]],
        top_k: int = 3,
    ) -> list[RecommendedCombination]:
        """Recommend strategy combinations.

        Args:
            data: Historical kline events for regime detection and correlation.
            tuning_results: List of tuning result dicts (from JSON files).
                Each should have: strategy_id, interval, best_sharpe, etc.
            top_k: Number of combinations to return.

        Returns:
            List of RecommendedCombination sorted by diversity score.
        """
        import pandas as pd

        # Step 1: Detect regime
        prices = pd.Series([e.close for e in data if e.closed])
        regime = detect_market_regime(prices)

        # Step 2: Build strategy profiles from tuning results
        profiles = self._build_profiles(tuning_results)

        # Step 3: Filter by quality and regime fitness
        candidates = self._filter_candidates(profiles, regime)

        if len(candidates) < self.combo_size[0]:
            # Not enough candidates, relax filters
            candidates = self._filter_candidates(profiles, regime, relaxed=True)

        if not candidates:
            return []

        # Step 4: Compute signal correlation if data available
        corr_matrix = self._compute_correlations(candidates, data)

        # Step 5: Generate combinations and score by diversity
        combos = self._generate_combos(candidates, corr_matrix, regime)

        # Return top-K
        combos.sort(key=lambda c: c.diversity_score, reverse=True)
        return combos[:top_k]

    def _build_profiles(
        self,
        tuning_results: list[dict[str, Any]],
    ) -> list[StrategyProfile]:
        """Build strategy profiles from tuning result JSON."""
        from selene_space_strategy.factory import STRATEGY_TEMPLATES

        profiles: list[StrategyProfile] = []
        seen: set[str] = set()

        for result in tuning_results:
            sid = result.get("strategy_id", result.get("strategy", ""))
            interval = result.get("interval", "1d")

            # Lookup category from factory templates
            category = result.get("category", "")
            if not category and sid in STRATEGY_TEMPLATES:
                category = STRATEGY_TEMPLATES[sid].category.value

            # Deduplicate by strategy+interval (keep best)
            key = f"{sid}_{interval}"
            sharpe = result.get("best_sharpe", result.get("sharpe", 0.0))

            if key in seen:
                existing = next((p for p in profiles if f"{p.strategy_id}_{p.interval}" == key), None)
                if existing and sharpe > existing.sharpe:
                    existing.sharpe = sharpe
                    existing.total_return = result.get("total_return", result.get("best_return", 0.0))
                    existing.max_drawdown = result.get("max_drawdown", 0.0)
                    existing.win_rate = result.get("win_rate", 0.0)
                    existing.profit_factor = result.get("profit_factor", 0.0)
                    existing.total_trades = result.get("total_trades", 0)
                    existing.params = result.get("best_params", result.get("params", {}))
                    if category:
                        existing.category = category
                continue

            seen.add(key)
            profiles.append(StrategyProfile(
                strategy_id=sid,
                strategy_name=result.get("strategy_name", result.get("strategy", sid)),
                category=category,
                interval=interval,
                sharpe=sharpe,
                total_return=result.get("total_return", result.get("best_return", 0.0)),
                max_drawdown=result.get("max_drawdown", 0.0),
                win_rate=result.get("win_rate", 0.0),
                profit_factor=result.get("profit_factor", 0.0),
                total_trades=result.get("total_trades", 0),
                params=result.get("best_params", result.get("params", {})),
            ))

        return profiles

    def _filter_candidates(
        self,
        profiles: list[StrategyProfile],
        regime: MarketRegime,
        relaxed: bool = False,
    ) -> list[StrategyProfile]:
        """Filter strategies by quality and regime fitness."""
        min_sharpe = self.min_sharpe if not relaxed else -1.0
        min_trades = self.min_trades if not relaxed else 0

        candidates = [
            p for p in profiles
            if p.sharpe >= min_sharpe and p.total_trades >= min_trades
        ]

        # Boost regime-fitting strategies
        regime_weights = _DEFAULT_REGIME_WEIGHTS.get(regime, {})
        for p in candidates:
            regime_w = regime_weights.get(p.strategy_id, 1.0)
            # Boost quality score by regime fitness
            p._regime_boost = regime_w

        # Sort by quality × regime boost
        candidates.sort(
            key=lambda p: p.quality_score * getattr(p, "_regime_boost", 1.0),
            reverse=True,
        )
        return candidates

    def _compute_correlations(
        self,
        candidates: list[StrategyProfile],
        data: list[KlineEvent],
    ) -> dict[str, dict[str, float]]:
        """Compute signal correlation matrix between candidates.

        Uses a simple approach: run each strategy on data, collect
        BUY/SELL signals per bar, then compute correlation.

        For efficiency, only compute if data is available and strategies
        are importable. Returns empty matrix otherwise.
        """
        # Simplified: estimate correlation from category overlap
        # Full implementation would run strategies and compute actual signal correlation
        corr: dict[str, dict[str, float]] = {}
        for p1 in candidates:
            corr[p1.strategy_id] = {}
            for p2 in candidates:
                if p1.strategy_id == p2.strategy_id:
                    corr[p1.strategy_id][p2.strategy_id] = 1.0
                elif p2.strategy_id in corr and p1.strategy_id in corr[p2.strategy_id]:
                    corr[p1.strategy_id][p2.strategy_id] = corr[p2.strategy_id][p1.strategy_id]
                else:
                    # Estimate from category distance
                    c1 = p1.category or "trend"
                    c2 = p2.category or "trend"
                    # Same category → higher correlation
                    cat_dist = _CATEGORY_DISTANCE.get(c1, {}).get(c2, 0.5)
                    # Correlation inversely related to category distance
                    corr[p1.strategy_id][p2.strategy_id] = max(0.0, 1.0 - cat_dist)

        return corr

    def _generate_combos(
        self,
        candidates: list[StrategyProfile],
        corr_matrix: dict[str, dict[str, float]],
        regime: MarketRegime,
    ) -> list[RecommendedCombination]:
        """Generate diverse strategy combinations using greedy selection."""
        from itertools import combinations

        min_size, max_size = self.combo_size
        combos: list[RecommendedCombination] = []

        # Limit candidate pool to top performers
        pool = candidates[:15]  # At most 15 candidates

        for size in range(min_size, min(max_size + 1, len(pool) + 1)):
            for combo in combinations(pool, size):
                sids = [p.strategy_id for p in combo]

                # Check max correlation constraint
                max_corr = 0.0
                total_corr = 0.0
                pair_count = 0
                valid = True

                for i in range(len(sids)):
                    for j in range(i + 1, len(sids)):
                        c = corr_matrix.get(sids[i], {}).get(sids[j], 0.5)
                        if c > self.max_correlation:
                            valid = False
                            break
                        max_corr = max(max_corr, c)
                        total_corr += c
                        pair_count += 1
                    if not valid:
                        break

                if not valid:
                    continue

                avg_corr = total_corr / pair_count if pair_count > 0 else 0.0

                # Diversity score: low avg correlation + category spread
                categories = set(p.category for p in combo)
                category_diversity = len(categories) / size
                diversity_score = (1.0 - avg_corr) * 0.7 + category_diversity * 0.3

                # Assign weights from regime presets
                regime_weights = _DEFAULT_REGIME_WEIGHTS.get(regime, {})
                weights: dict[str, float] = {}
                for p in combo:
                    weights[p.strategy_id] = regime_weights.get(p.strategy_id, 1.0)

                # Estimate performance range
                sharpes = [p.sharpe for p in combo if p.sharpe > 0]
                mdds = [p.max_drawdown for p in combo if p.max_drawdown < 0]
                exp_sharpe = (
                    min(sharpes) * 0.7 if sharpes else 0.0,
                    max(sharpes) * 1.1 if sharpes else 0.0,
                )
                # MDD range: worst (more negative) to best (less negative)
                exp_mdd = (
                    min(mdds) * 1.2 if mdds else 0.0,
                    max(mdds) * 0.8 if mdds else 0.0,
                )

                combos.append(RecommendedCombination(
                    strategies=list(combo),
                    weights=weights,
                    regime=regime,
                    diversity_score=diversity_score,
                    avg_correlation=avg_corr,
                    expected_sharpe_range=exp_sharpe,
                    expected_mdd_range=exp_mdd,
                ))

        return combos


def load_tuning_results(json_path: str) -> list[dict[str, Any]]:
    """Load and flatten tuning results from JSON file.

    Handles both single-result and multi-result formats.

    Args:
        json_path: Path to tuning results JSON file.

    Returns:
        List of result dicts with normalized keys.
    """
    import json

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Handle different formats
    if "results" in data:
        raw = data["results"]
    elif isinstance(data, list):
        raw = data
    else:
        raw = [data]

    results: list[dict[str, Any]] = []
    for item in raw:
        # Flatten nested backtest_result if present
        if "backtest_result" in item and isinstance(item["backtest_result"], dict):
            flat = {**item, **item["backtest_result"]}
            flat.pop("backtest_result", None)
            flat.pop("equity_curve", None)
            results.append(flat)
        else:
            results.append(item)

    return results
