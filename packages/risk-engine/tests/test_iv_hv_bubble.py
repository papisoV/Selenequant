"""Tests for IvHvBubbleRule — IV/HV options bubble index (advisory alert)."""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_risk.advanced_rules import IvHvBubbleRule
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _make_intent(**overrides: Any) -> TradeIntent:
    defaults = {
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "quantity": 1.0,
        "price": 67000.0,
        "order_type": OrderType.MARKET,
        "reasoning": "test",
        "confidence": 0.8,
        "risk_params": {"position_fraction": 0.1},
        "timestamp": 0,
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


def _noisy_returns(n: int, amp: float) -> list[float]:
    """Oscillating returns with stdev ≈ amp per period (n-1 correction).

    With 252 periods/year, amp=0.01 gives hv ≈ 0.01 * sqrt(252) ≈ 0.1615.
    """
    return [amp if i % 2 == 0 else -amp for i in range(n)]


class TestIvHvBubbleBasics:
    def test_iv_premium_emits_warning(self) -> None:
        """ratio = 0.65 / 0.1615 ≈ 4.03 > 1.01 → WARNING (sell premium)."""
        rule = IvHvBubbleRule()
        ctx = {"iv_annualized": 0.65, "returns": _noisy_returns(30, 0.01)}
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.level == RiskLevel.WARNING
        assert result.rule_id == "iv_hv_bubble"
        assert result.context["ratio"] == pytest.approx(4.0258, rel=0.01)
        assert result.context["iv"] == 0.65
        assert result.context["hv"] > 0

    def test_iv_discount_emits_info(self) -> None:
        """ratio < 0.85 → INFO (buy options)."""
        rule = IvHvBubbleRule()
        # amp=0.03: hv ≈ 0.03 * sqrt(252) ≈ 0.4762; iv=0.10 → ratio≈0.21
        ctx = {"iv_annualized": 0.10, "returns": _noisy_returns(30, 0.03)}
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.level == RiskLevel.INFO

    def test_ratio_in_band_returns_none(self) -> None:
        """ratio between 0.85 and 1.01 → None (rule silent)."""
        rule = IvHvBubbleRule()
        # Use wide thresholds so ratio≈4.03 lands inside the band
        rule2 = IvHvBubbleRule(sell_threshold=10.0, buy_threshold=0.001)
        ctx = {"iv_annualized": 0.65, "returns": _noisy_returns(30, 0.01)}
        assert rule2.check(_make_intent(), ctx) is None
        # Sanity: default thresholds would fire WARNING for the same ctx
        assert rule.check(_make_intent(), ctx) is not None

    def test_missing_iv_returns_none(self) -> None:
        rule = IvHvBubbleRule()
        assert rule.check(_make_intent(), {"returns": _noisy_returns(30, 0.01)}) is None
        assert (
            rule.check(
                _make_intent(),
                {"iv_annualized": None, "returns": _noisy_returns(30, 0.01)},
            )
            is None
        )
        assert (
            rule.check(
                _make_intent(),
                {"iv_annualized": 0.0, "returns": _noisy_returns(30, 0.01)},
            )
            is None
        )

    def test_insufficient_returns_returns_none(self) -> None:
        rule = IvHvBubbleRule(min_returns_for_vol=50)
        ctx = {"iv_annualized": 0.65, "returns": _noisy_returns(30, 0.01)}
        assert rule.check(_make_intent(), ctx) is None
        # Zero returns list too
        assert (
            rule.check(_make_intent(), {"iv_annualized": 0.65, "returns": []}) is None
        )

    def test_thresholds_configurable(self) -> None:
        rule = IvHvBubbleRule(sell_threshold=3.0, buy_threshold=0.5)
        # ratio ≈ 4.03 > 3.0 → WARNING with custom sell threshold
        ctx = {"iv_annualized": 0.65, "returns": _noisy_returns(30, 0.01)}
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.level == RiskLevel.WARNING
        # ratio ≈ 0.65/0.1615 lands between 0.5 and 3.0 with reduced iv → silent
        ctx_quiet = {"iv_annualized": 0.35, "returns": _noisy_returns(30, 0.01)}
        # ratio ≈ 2.17, inside [0.5, 3.0] → None
        assert rule.check(_make_intent(), ctx_quiet) is None
