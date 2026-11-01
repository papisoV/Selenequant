"""Tests for dynamic capital allocator.

Covers:
  - Kelly allocator (Sharpe-based)
  - Risk parity allocator (equal risk contribution)
  - Drawdown-based allocator (inverse drawdown weighting)
"""

from __future__ import annotations

import pytest

from selene_space_alpha.allocator.drawdown_allocator import DrawdownBasedAllocator
from selene_space_alpha.allocator.kelly_allocator import KellyAllocator
from selene_space_alpha.allocator.risk_parity_allocator import RiskParityAllocator


# ---------------------------------------------------------------------------
# Kelly Allocator
# ---------------------------------------------------------------------------


class TestKellyAllocator:
    def test_higher_sharpe_gets_more_capital(self) -> None:
        allocator = KellyAllocator(max_leverage=2.0)
        strategies = {
            "strat_a": {"sharpe": 2.0, "volatility": 0.15},
            "strat_b": {"sharpe": 1.0, "volatility": 0.15},
            "strat_c": {"sharpe": 0.5, "volatility": 0.15},
        }
        weights = allocator.allocate(strategies)
        assert weights["strat_a"] > weights["strat_c"]

    def test_weights_sum_to_one(self) -> None:
        allocator = KellyAllocator()
        strategies = {
            "a": {"sharpe": 1.5, "volatility": 0.10},
            "b": {"sharpe": 1.0, "volatility": 0.12},
            "c": {"sharpe": 0.8, "volatility": 0.08},
        }
        weights = allocator.allocate(strategies)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_single_strategy_gets_all(self) -> None:
        allocator = KellyAllocator()
        strategies = {"only": {"sharpe": 1.0, "volatility": 0.10}}
        weights = allocator.allocate(strategies)
        assert weights["only"] == pytest.approx(1.0)

    def test_negative_sharpe_gets_zero(self) -> None:
        allocator = KellyAllocator()
        strategies = {
            "good": {"sharpe": 1.5, "volatility": 0.10},
            "bad": {"sharpe": -0.5, "volatility": 0.10},
        }
        weights = allocator.allocate(strategies)
        assert weights.get("bad", 0.0) == 0.0

    def test_empty_strategies(self) -> None:
        allocator = KellyAllocator()
        weights = allocator.allocate({})
        assert weights == {}

    def test_max_leverage_caps_exposure(self) -> None:
        allocator = KellyAllocator(max_leverage=1.0)
        strategies = {
            "a": {"sharpe": 3.0, "volatility": 0.10},  # Kelly fraction would be 30
        }
        weights = allocator.allocate(strategies)
        assert weights["a"] <= 1.0


# ---------------------------------------------------------------------------
# Risk Parity Allocator
# ---------------------------------------------------------------------------


class TestRiskParityAllocator:
    def test_equal_vol_gives_equal_weight(self) -> None:
        allocator = RiskParityAllocator()
        strategies = {
            "a": {"volatility": 0.10},
            "b": {"volatility": 0.10},
        }
        weights = allocator.allocate(strategies)
        assert weights["a"] == pytest.approx(0.5)
        assert weights["b"] == pytest.approx(0.5)

    def test_higher_vol_gets_lower_weight(self) -> None:
        allocator = RiskParityAllocator()
        strategies = {
            "low_vol": {"volatility": 0.10},
            "high_vol": {"volatility": 0.30},
        }
        weights = allocator.allocate(strategies)
        assert weights["low_vol"] > weights["high_vol"]

    def test_weights_sum_to_one(self) -> None:
        allocator = RiskParityAllocator()
        strategies = {
            "a": {"volatility": 0.15},
            "b": {"volatility": 0.20},
            "c": {"volatility": 0.25},
        }
        weights = allocator.allocate(strategies)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_zero_vol_handled(self) -> None:
        allocator = RiskParityAllocator()
        strategies = {
            "a": {"volatility": 0.0},
            "b": {"volatility": 0.10},
        }
        weights = allocator.allocate(strategies)
        # Strategy with zero vol gets 0 weight
        assert weights.get("a", 0.0) == 0.0


# ---------------------------------------------------------------------------
# Drawdown-Based Allocator
# ---------------------------------------------------------------------------


class TestDrawdownBasedAllocator:
    def test_larger_drawdown_gets_lower_weight(self) -> None:
        allocator = DrawdownBasedAllocator()
        strategies = {
            "strat_a": {"current_drawdown": 0.05},
            "strat_b": {"current_drawdown": 0.20},
        }
        weights = allocator.allocate(strategies)
        assert weights["strat_a"] > weights["strat_b"]

    def test_zero_drawdown_strategies_equal(self) -> None:
        allocator = DrawdownBasedAllocator()
        strategies = {
            "a": {"current_drawdown": 0.0},
            "b": {"current_drawdown": 0.0},
        }
        weights = allocator.allocate(strategies)
        assert weights["a"] == pytest.approx(0.5)

    def test_weights_sum_to_one(self) -> None:
        allocator = DrawdownBasedAllocator()
        strategies = {
            "a": {"current_drawdown": 0.10},
            "b": {"current_drawdown": 0.05},
            "c": {"current_drawdown": 0.15},
        }
        weights = allocator.allocate(strategies)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_full_drawdown_gets_zero(self) -> None:
        allocator = DrawdownBasedAllocator()
        strategies = {
            "alive": {"current_drawdown": 0.10},
            "dead": {"current_drawdown": 0.50},
        }
        weights = allocator.allocate(strategies, max_drawdown_cap=0.50)
        assert weights["dead"] == pytest.approx(0.0)
