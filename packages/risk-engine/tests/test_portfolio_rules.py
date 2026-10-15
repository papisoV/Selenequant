"""Tests for portfolio-level risk rules.

Covers:
  - TotalExposureRule: gross notional / equity
  - NetExposureRule: (longs - shorts) / equity
  - MarginUtilizationRule: true margin / equity
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from selene_space_risk.portfolio_rules import (
    MarginUtilizationRule,
    NetExposureRule,
    TotalExposureRule,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _make_intent(**overrides: Any) -> TradeIntent:
    defaults = {
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "quantity": 0.1,
        "price": 67000.0,
        "order_type": OrderType.MARKET,
        "reasoning": "test",
        "confidence": 0.8,
        "risk_params": {"stop_loss": 65000.0, "take_profit": 70000.0},
        "timestamp": int(time.time() * 1000),
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


# ---------------------------------------------------------------------------
# TotalExposureRule
# ---------------------------------------------------------------------------


class TestTotalExposureRule:
    """Gross notional exposure = sum(abs(notional)) / equity."""

    def test_passes_within_limit(self) -> None:
        rule = TotalExposureRule(max_gross_exposure=2.0)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 50_000.0, "ETH/USDT": 30_000.0},
        }
        intent = _make_intent(symbol="SOL/USDT", quantity=100, price=100)
        result = rule.check(intent, ctx)
        assert result is None

    def test_alerts_over_limit(self) -> None:
        rule = TotalExposureRule(max_gross_exposure=1.0)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 50_000.0, "ETH/USDT": 30_000.0},
        }
        intent = _make_intent(symbol="SOL/USDT", quantity=100, price=300)
        result = rule.check(intent, ctx)
        assert isinstance(result, RiskAlert)
        assert result.rule_id == "total_exposure"
        assert result.level == RiskLevel.DANGEROUS
        # 50k + 30k + 30k = 110k -> 110% > 100%
        assert result.context["gross_exposure_pct"] > 1.0

    def test_five_positions_at_19_pct_each_trigger(self) -> None:
        """Regression: 5 positions at 19% each = 95% gross — should alert at 80% threshold."""
        rule = TotalExposureRule(max_gross_exposure=0.80)
        equity = 100_000.0
        positions = {
            "BTC/USDT": 19_000.0,
            "ETH/USDT": 19_000.0,
            "SOL/USDT": 19_000.0,
            "AVAX/USDT": 19_000.0,
        }
        intent = _make_intent(symbol="MATIC/USDT", quantity=1, price=19_000)
        ctx = {"total_equity": equity, "positions": positions}
        result = rule.check(intent, ctx)
        assert isinstance(result, RiskAlert)
        # 4×19k + 19k = 95k -> 95% > 80%
        assert result.context["gross_exposure_pct"] == pytest.approx(0.95, abs=0.01)

    def test_passes_when_no_equity(self) -> None:
        rule = TotalExposureRule()
        result = rule.check(_make_intent(), {"total_equity": 0, "positions": {}})
        assert result is None

    def test_passes_when_no_positions_and_small_trade(self) -> None:
        rule = TotalExposureRule(max_gross_exposure=1.0)
        ctx = {"total_equity": 100_000.0, "positions": {}}
        intent = _make_intent(quantity=0.1, price=67_000)
        result = rule.check(intent, ctx)
        assert result is None  # 6700 / 100000 = 6.7%

    def test_leverage_amplifies_exposure(self) -> None:
        rule = TotalExposureRule(max_gross_exposure=1.0)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 80_000.0},
        }
        # existing 80k + new 20k × 2x leverage = 120k → 120% > 100%
        intent = _make_intent(
            symbol="ETH/USDT",
            quantity=1,
            price=20_000,
            risk_params={"leverage": 2, "stop_loss": 19000.0},
        )
        result = rule.check(intent, ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["gross_exposure_pct"] > 1.0


# ---------------------------------------------------------------------------
# NetExposureRule
# ---------------------------------------------------------------------------


class TestNetExposureRule:
    """Net directional exposure = (longs - shorts) / equity."""

    def test_passes_within_range(self) -> None:
        rule = NetExposureRule(max_net_long=1.0, max_net_short=-1.0)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 40_000.0, "ETH/USDT": -10_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert result is None  # net = 30% long

    def test_alerts_too_long(self) -> None:
        rule = NetExposureRule(max_net_long=0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 60_000.0, "ETH/USDT": 5_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.rule_id == "net_exposure"
        assert result.context["net_exposure_pct"] > 0.50

    def test_alerts_too_short(self) -> None:
        rule = NetExposureRule(max_net_short=-0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": -70_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.rule_id == "net_exposure"
        assert result.context["net_exposure_pct"] < -0.50

    def test_hedged_portfolio_passes(self) -> None:
        """Long 50k + short 50k = net 0, should pass even tight limits."""
        rule = NetExposureRule(max_net_long=0.20, max_net_short=-0.20)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 50_000.0, "ETH/USDT": -50_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert result is None

    def test_passes_when_no_equity(self) -> None:
        rule = NetExposureRule()
        result = rule.check(_make_intent(), {"total_equity": 0, "positions": {}})
        assert result is None


# ---------------------------------------------------------------------------
# MarginUtilizationRule
# ---------------------------------------------------------------------------


class TestMarginUtilizationRule:
    """Margin utilization = total_margin / equity."""

    def test_passes_low_margin(self) -> None:
        rule = MarginUtilizationRule(max_margin_utilization=0.90)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "BTC/USDT": {
                    "notional": 50_000.0,
                    "margin": 5_000.0,
                },
                "ETH/USDT": {
                    "notional": 30_000.0,
                    "margin": 3_000.0,
                },
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert result is None  # 8k / 100k = 8%

    def test_alerts_high_margin(self) -> None:
        rule = MarginUtilizationRule(max_margin_utilization=0.10)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "BTC/USDT": {
                    "notional": 100_000.0,
                    "margin": 10_000.0,
                },
                "ETH/USDT": {
                    "notional": 50_000.0,
                    "margin": 5_000.0,
                },
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.rule_id == "margin_utilization"
        assert result.context["margin_utilization_pct"] > 0.10

    def test_critical_when_margin_exceeds_equity(self) -> None:
        rule = MarginUtilizationRule(max_margin_utilization=0.90)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "BTC/USDT": {
                    "notional": 200_000.0,
                    "margin": 100_000.0,
                },
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.level == RiskLevel.CRITICAL

    def test_falls_back_to_notional_proxy_when_no_margin_field(self) -> None:
        """When positions are dict[str, float] (legacy), use notional / leverage as margin proxy."""
        rule = MarginUtilizationRule(max_margin_utilization=0.50, default_leverage=10.0)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 80_000.0},
        }
        # proxy margin = 80k / 10 = 8k → 8% < 50%, should pass
        result = rule.check(_make_intent(), ctx)
        assert result is None

    def test_falls_back_to_notional_proxy_triggers_alert(self) -> None:
        rule = MarginUtilizationRule(max_margin_utilization=0.05, default_leverage=10.0)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"BTC/USDT": 80_000.0},
        }
        # proxy margin = 80k / 10 = 8k → 8% > 5%
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.rule_id == "margin_utilization"

    def test_passes_when_no_equity(self) -> None:
        rule = MarginUtilizationRule()
        result = rule.check(_make_intent(), {"total_equity": 0, "positions": {}})
        assert result is None
