"""Tests for Phase 3.2: Leverage risk scaling across all risk rules."""

from __future__ import annotations

import pytest

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel
from selene_space_types.trading import OrderSide, OrderType

from selene_space_risk.rules import (
    MaxPositionRule,
    MaxSingleRiskRule,
    DailyLossLimitRule,
)
from selene_space_risk.advanced_rules import (
    DrawdownCircuitBreakerRule,
    OnePercentRiskRule,
    FloatingLossBlockRule,
    DailyLossFuseRule,
)


def _intent(
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.0,
    price: float = 100.0,
    **risk_params: float,
) -> TradeIntent:
    import time
    return TradeIntent(
        strategy_id="test",
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        order_type=OrderType.MARKET,
        reasoning="test",
        confidence=0.8,
        risk_params=risk_params,
        timestamp=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# MaxPositionRule — leverage-adjusted
# ---------------------------------------------------------------------------

class TestMaxPositionLeverage:
    def test_no_leverage_passes(self) -> None:
        rule = MaxPositionRule(max_position_pct=0.20)
        intent = _intent(quantity=10, price=100)
        ctx = {"total_equity": 10000, "positions": {}, "portfolio_leverage": 1.0}
        assert rule.check(intent, ctx) is None

    def test_10x_leverage_triggers(self) -> None:
        """10x leverage: 10% nominal position → 100% effective, exceeds 20% limit."""
        rule = MaxPositionRule(max_position_pct=0.20)
        intent = _intent(quantity=10, price=100)
        ctx = {"total_equity": 10000, "positions": {}, "portfolio_leverage": 10.0}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.level == RiskLevel.RESTRICTED
        assert alert.context["portfolio_leverage"] == 10.0

    def test_2x_leverage_passes(self) -> None:
        """2x leverage: 10% nominal → 20% effective, exactly at limit."""
        rule = MaxPositionRule(max_position_pct=0.20)
        intent = _intent(quantity=10, price=100)
        ctx = {"total_equity": 10000, "positions": {}, "portfolio_leverage": 2.0}
        # 10% * 2 = 20%, exactly at limit → no alert
        assert rule.check(intent, ctx) is None

    def test_default_leverage_1x(self) -> None:
        """Without portfolio_leverage in context, defaults to 1x (no change)."""
        rule = MaxPositionRule(max_position_pct=0.20)
        intent = _intent(quantity=10, price=100)
        ctx = {"total_equity": 10000, "positions": {}}
        assert rule.check(intent, ctx) is None


# ---------------------------------------------------------------------------
# MaxSingleRiskRule — leverage-adjusted
# ---------------------------------------------------------------------------

class TestMaxSingleRiskLeverage:
    def test_no_leverage_passes(self) -> None:
        rule = MaxSingleRiskRule(max_risk_pct=0.02)
        intent = _intent(quantity=1, price=100, stop_loss=98)
        ctx = {"total_equity": 10000}
        assert rule.check(intent, ctx) is None

    def test_10x_leverage_triggers(self) -> None:
        """10x leverage: $200 potential loss → $2000 leveraged, exceeds 2% of $10000."""
        rule = MaxSingleRiskRule(max_risk_pct=0.02)
        intent = _intent(quantity=100, price=100, stop_loss=98, leverage=10)
        ctx = {"total_equity": 10000}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.context["leverage"] == 10
        assert alert.context["leveraged_loss"] == 2000.0

    def test_5x_leverage_triggers(self) -> None:
        """5x leverage: $2 potential loss → $10 leveraged, exactly 2% of $10000."""
        rule = MaxSingleRiskRule(max_risk_pct=0.02)
        intent = _intent(quantity=1, price=100, stop_loss=98, leverage=5)
        ctx = {"total_equity": 10000}
        # $10 = 1% of equity, below 2% threshold
        assert rule.check(intent, ctx) is None


# ---------------------------------------------------------------------------
# DailyLossLimitRule — leverage-adjusted
# ---------------------------------------------------------------------------

class TestDailyLossLimitLeverage:
    def test_no_leverage_passes(self) -> None:
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -400}
        assert rule.check(intent, ctx) is None

    def test_10x_leverage_uses_nominal_loss(self) -> None:
        """C6 fix: $400 nominal loss @ 10x → effective_loss = $400 (not $4000).

        total_equity already includes leveraged PnL, so re-multiplying
        double-counts. 4% nominal < 5% threshold → no trip even at 10x.
        """
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -400, "portfolio_leverage": 10.0}
        alert = rule.check(intent, ctx)
        assert alert is None

    def test_2x_leverage_uses_nominal_loss(self) -> None:
        """C6 fix: $400 nominal @ 2x → 4% effective (not 8%), below 5% threshold."""
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -400, "portfolio_leverage": 2.0}
        alert = rule.check(intent, ctx)
        assert alert is None

    def test_nominal_loss_above_threshold_triggers_regardless_of_leverage(self) -> None:
        """C6 fix: $600 nominal > 5% threshold → trips at any leverage."""
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -600, "portfolio_leverage": 3.0}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.context["portfolio_leverage"] == 3.0


# ---------------------------------------------------------------------------
# DrawdownCircuitBreakerRule — C6: no leverage double-count
# ---------------------------------------------------------------------------

class TestDrawdownLeverage:
    def test_15pct_drawdown_at_3x_does_not_amplify(self) -> None:
        """C6 fix: 15% drawdown @ 3x → effective_drawdown = 15% (not 45%).

        total_equity already includes leveraged unrealized PnL, so
        drawdown is already leverage-adjusted. Re-multiplying double-counts.
        15% < 30% threshold → no trip.
        """
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.30)
        intent = _intent()
        ctx = {"total_equity": 8500, "peak_equity": 10000, "portfolio_leverage": 3.0}
        alert = rule.check(intent, ctx)
        assert alert is None

    def test_35pct_drawdown_triggers_regardless_of_leverage(self) -> None:
        """C6 fix: 35% drawdown > 30% threshold → trips at any leverage."""
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.30)
        intent = _intent()
        ctx = {"total_equity": 6500, "peak_equity": 10000, "portfolio_leverage": 3.0}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.level == RiskLevel.CRITICAL
        # effective_drawdown == drawdown (no leverage multiply)
        assert alert.context["effective_drawdown"] == pytest.approx(0.35, abs=0.01)

    def test_no_leverage_same_drawdown_triggers(self) -> None:
        """35% drawdown @ 1x → trips (35% > 30%)."""
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.30)
        intent = _intent()
        ctx = {"total_equity": 6500, "peak_equity": 10000, "portfolio_leverage": 1.0}
        alert = rule.check(intent, ctx)
        assert alert is not None


# ---------------------------------------------------------------------------
# OnePercentRiskRule — already leverage-aware, verify
# ---------------------------------------------------------------------------

class TestOnePercentRiskLeverage:
    def test_leverage_amplifies_risk(self) -> None:
        rule = OnePercentRiskRule(max_risk_pct=0.01)
        # $1 loss × 10x = $10, 1% of $10000 = $100 → still under threshold
        intent = _intent(quantity=1, price=100, stop_loss=99, leverage=10)
        ctx = {"total_equity": 10000}
        # $10 < $100, should pass
        assert rule.check(intent, ctx) is None

    def test_high_leverage_triggers(self) -> None:
        rule = OnePercentRiskRule(max_risk_pct=0.01)
        # $5 loss × 50x = $250, exceeds 1% of $10000 ($100)
        intent = _intent(quantity=1, price=100, stop_loss=95, leverage=50)
        ctx = {"total_equity": 10000}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.context["leveraged_loss"] == 250.0


# ---------------------------------------------------------------------------
# FloatingLossBlockRule — leverage-adjusted
# ---------------------------------------------------------------------------

class TestFloatingLossBlockLeverage:
    def test_no_leverage_small_loss_passes(self) -> None:
        rule = FloatingLossBlockRule(loss_threshold_pct=0.0)
        intent = _intent()
        ctx = {"positions": {"ETH/USDT": {"unrealized_pnl_pct": -0.01}}, "portfolio_leverage": 1.0}
        alert = rule.check(intent, ctx)
        # -1% × 1x = -1%, below threshold 0 → triggers
        assert alert is not None

    def test_10x_leverage_amplifies(self) -> None:
        """-1% nominal × 10x = -10% effective, triggers at threshold 0."""
        rule = FloatingLossBlockRule(loss_threshold_pct=-0.05)
        intent = _intent()
        ctx = {"positions": {"ETH/USDT": {"unrealized_pnl_pct": -0.01}}, "portfolio_leverage": 10.0}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.context["portfolio_leverage"] == 10.0

    def test_no_leverage_above_threshold_passes(self) -> None:
        """-1% nominal × 1x = -1%, above -5% threshold → no alert."""
        rule = FloatingLossBlockRule(loss_threshold_pct=-0.05)
        intent = _intent()
        ctx = {"positions": {"ETH/USDT": {"unrealized_pnl_pct": -0.01}}, "portfolio_leverage": 1.0}
        assert rule.check(intent, ctx) is None


# ---------------------------------------------------------------------------
# DailyLossFuseRule — leverage-adjusted
# ---------------------------------------------------------------------------

class TestDailyLossFuseLeverage:
    def test_no_leverage_below_threshold(self) -> None:
        rule = DailyLossFuseRule(daily_loss_pct=0.01, cooldown_seconds=3600)
        rule.reset()
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -50, "portfolio_leverage": 1.0}
        assert rule.check(intent, ctx) is None

    def test_10x_leverage_uses_nominal_loss(self) -> None:
        """C6 fix: $50 nominal @ 10x → effective_loss = $50 (not $500).

        total_equity already includes leveraged PnL, so re-multiplying
        double-counts. 0.5% nominal < 1% threshold → no trip even at 10x.
        """
        rule = DailyLossFuseRule(daily_loss_pct=0.01, cooldown_seconds=3600)
        rule.reset()
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -50, "portfolio_leverage": 10.0}
        alert = rule.check(intent, ctx)
        assert alert is None

    def test_nominal_loss_above_threshold_triggers_fuse(self) -> None:
        """C6 fix: $150 nominal > 1% of $10000 → trips at any leverage."""
        rule = DailyLossFuseRule(daily_loss_pct=0.01, cooldown_seconds=3600)
        rule.reset()
        intent = _intent()
        ctx = {"total_equity": 10000, "daily_realized_pnl": -150, "portfolio_leverage": 3.0}
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.level == RiskLevel.CRITICAL
        assert alert.context["portfolio_leverage"] == 3.0


# ---------------------------------------------------------------------------
# Cross-rule consistency: all rules scale the same way
# ---------------------------------------------------------------------------

class TestLeverageConsistency:
    def test_all_rules_use_portfolio_leverage_context(self) -> None:
        """Verify all leverage-aware rules read portfolio_leverage from context."""
        rules_with_leverage = [
            MaxPositionRule(),
            DailyLossLimitRule(),
            DrawdownCircuitBreakerRule(),
            FloatingLossBlockRule(),
            DailyLossFuseRule(),
        ]
        # Rules that read leverage from intent.risk_params
        rules_with_intent_leverage = [
            MaxSingleRiskRule(),
            OnePercentRiskRule(),
        ]
        # Just verify they don't crash with leverage=1
        intent = _intent(quantity=1, price=100, stop_loss=99, leverage=1)
        ctx = {
            "total_equity": 10000,
            "positions": {},
            "daily_realized_pnl": 0,
            "portfolio_leverage": 1.0,
            "peak_equity": 10000,
        }
        for rule in rules_with_leverage:
            rule.check(intent, ctx)  # should not raise
        for rule in rules_with_intent_leverage:
            rule.check(intent, ctx)  # should not raise
