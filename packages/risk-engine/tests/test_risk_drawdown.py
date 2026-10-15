"""C6 RED→GREEN test: leverage double-count removal.

Before fix: effective_drawdown = drawdown * portfolio_leverage → 15% drop @ 3x
trips the 15% breaker at 45% effective (false trip at nominal 5%).
After fix: effective_drawdown = drawdown → 15% drop @ 3x trips at 15% nominal,
NOT 45%. total_equity already includes leveraged unrealized PnL, so re-multiplying
double-counts.
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_risk.advanced_rules import (
    DailyLossFuseRule,
    DrawdownCircuitBreakerRule,
)
from selene_space_risk.rules import DailyLossLimitRule
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _make_intent(**overrides: Any) -> TradeIntent:
    defaults: dict[str, Any] = {
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


class TestDrawdownCircuitBreakerNoLeverageDoubleCount:
    def test_15pct_drop_at_3x_leverage_trips_at_15pct_not_45pct(self) -> None:
        """15% drop @ 3x leverage → breaker trips at 15%, NOT 45%.

        Before fix: drawdown * 3 = 45% > 15% threshold → trips on a 5% nominal drop.
        After fix: drawdown = 15% → trips exactly at 15% nominal.
        """
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.15)
        intent = _make_intent()
        # peak=100, current=85 → drawdown=15% (NOT 45%)
        ctx = {
            "total_equity": 85.0,
            "peak_equity": 100.0,
            "portfolio_leverage": 3.0,
        }
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.level == RiskLevel.CRITICAL
        # Effective drawdown must equal nominal (no leverage multiply).
        assert alert.context["effective_drawdown"] == 0.15
        assert alert.context["drawdown"] == 0.15

    def test_14pct_drop_at_3x_leverage_does_not_trip(self) -> None:
        """14% drop @ 3x → no trip (before fix: 14*3=42% > 15% → false trip)."""
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.15)
        intent = _make_intent()
        ctx = {
            "total_equity": 86.0,
            "peak_equity": 100.0,
            "portfolio_leverage": 3.0,
        }
        alert = rule.check(intent, ctx)
        assert alert is None


class TestDailyLossFuseNoLeverageDoubleCount:
    def test_daily_loss_at_3x_leverage_uses_nominal(self) -> None:
        """Daily loss @ 3x leverage → effective_loss = nominal (not multiplied)."""
        rule = DailyLossFuseRule(daily_loss_pct=0.10, cooldown_seconds=60)
        intent = _make_intent()
        # total_equity=100, daily_pnl=-12 → 12% nominal loss > 10% threshold
        # Before fix: 12 * 3 = 36% → trips. After fix: 12% → trips.
        # Test the boundary: -9 nominal → 9% < 10% → no trip (before fix: 27% > 10% → false trip)
        ctx = {
            "total_equity": 100.0,
            "daily_realized_pnl": -9.0,
            "portfolio_leverage": 3.0,
        }
        alert = rule.check(intent, ctx)
        assert alert is None

    def test_daily_loss_at_3x_trips_at_nominal_threshold(self) -> None:
        rule = DailyLossFuseRule(daily_loss_pct=0.10, cooldown_seconds=60)
        intent = _make_intent()
        ctx = {
            "total_equity": 100.0,
            "daily_realized_pnl": -11.0,
            "portfolio_leverage": 3.0,
        }
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.context["effective_loss"] == 11.0


class TestDailyLossLimitNoLeverageDoubleCount:
    def test_daily_loss_limit_at_3x_uses_nominal(self) -> None:
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        intent = _make_intent()
        # -4 nominal on 100 equity = 4% < 5% threshold → no trip
        # Before fix: 4 * 3 = 12% > 5% → false trip
        ctx = {
            "total_equity": 100.0,
            "daily_realized_pnl": -4.0,
            "portfolio_leverage": 3.0,
        }
        alert = rule.check(intent, ctx)
        assert alert is None

    def test_daily_loss_limit_trips_at_nominal(self) -> None:
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        intent = _make_intent()
        ctx = {
            "total_equity": 100.0,
            "daily_realized_pnl": -6.0,
            "portfolio_leverage": 3.0,
        }
        alert = rule.check(intent, ctx)
        assert alert is not None
        assert alert.context["effective_loss"] == 6.0
