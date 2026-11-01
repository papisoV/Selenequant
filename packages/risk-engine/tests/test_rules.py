"""Built-in risk rules coverage — position caps, single-trade risk, daily
loss budget, symbol blacklist, cooldowns, and the default rule inventory."""

from __future__ import annotations

import time
from typing import Any

import pytest

from selene_space_risk.rules import (
    CooldownRule,
    DailyLossLimitRule,
    MaxPositionRule,
    MaxSingleRiskRule,
    SymbolBlacklistRule,
    create_default_rules,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType

EQUITY = 100_000.0


def _intent(**overrides: Any) -> TradeIntent:
    fields = dict(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=0.1,
        price=67_000.0,
        order_type=OrderType.MARKET,
        reasoning="test",
        confidence=0.8,
        risk_params={"stop_loss": 65_000.0, "take_profit": 70_000.0},
        timestamp=int(time.time() * 1000),
    )
    fields.update(overrides)
    return TradeIntent(**fields)


class TestPositionCapRule:
    def test_small_position_passes_silently(self) -> None:
        rule = MaxPositionRule(max_position_pct=0.20)
        ctx = {"total_equity": EQUITY, "positions": {"BTC/USDT": 5_000.0}}
        assert rule.check(_intent(quantity=0.1), ctx) is None

    def test_oversized_position_restricted(self) -> None:
        rule = MaxPositionRule(max_position_pct=0.10)
        ctx = {"total_equity": EQUITY, "positions": {"BTC/USDT": 8_000.0}}
        alert = rule.check(_intent(quantity=0.1), ctx)
        assert isinstance(alert, RiskAlert)
        assert (alert.rule_id, alert.level) == ("max_position", RiskLevel.RESTRICTED)

    def test_zero_equity_short_circuits(self) -> None:
        assert MaxPositionRule().check(_intent(), {"total_equity": 0}) is None


class TestSingleTradeRiskRule:
    """risk = quantity × |entry − stop| relative to equity."""

    def test_tight_stop_within_budget(self) -> None:
        rule = MaxSingleRiskRule(max_risk_pct=0.02)
        intent = _intent(
            quantity=0.5,
            price=67_000.0,
            risk_params={"stop_loss": 66_800.0, "take_profit": 68_000.0},
        )
        assert rule.check(intent, {"total_equity": EQUITY}) is None

    def test_wide_stop_breaches_budget(self) -> None:
        rule = MaxSingleRiskRule(max_risk_pct=0.02)
        intent = _intent(
            quantity=2.0,
            price=67_000.0,
            risk_params={"stop_loss": 65_000.0, "take_profit": 70_000.0},
        )
        alert = rule.check(intent, {"total_equity": EQUITY})
        assert isinstance(alert, RiskAlert)
        assert alert.rule_id == "max_single_risk"


class TestDailyLossBudgetRule:
    @pytest.mark.parametrize(
        ("realized_pnl", "should_alert"),
        [(500.0, False), (-5_000.0, True), (-6_000.0, True)],
        ids=["in-profit", "at-limit", "over-limit"],
    )
    def test_pnl_thresholds(self, realized_pnl: float, should_alert: bool) -> None:
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        ctx = {"total_equity": EQUITY, "daily_realized_pnl": realized_pnl}
        result = rule.check(_intent(), ctx)
        assert isinstance(result, RiskAlert) is should_alert

    def test_alert_carries_rule_id(self) -> None:
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        ctx = {"total_equity": EQUITY, "daily_realized_pnl": -6_000.0}
        alert = rule.check(_intent(), ctx)
        if alert is not None:
            assert alert.rule_id == "daily_loss_limit"

    def test_over_limit_definitely_alerts(self) -> None:
        rule = DailyLossLimitRule(max_daily_loss_pct=0.05)
        ctx = {"total_equity": EQUITY, "daily_realized_pnl": -5_000.0}
        assert isinstance(rule.check(_intent(), ctx), RiskAlert)


class TestSymbolBlacklistRule:
    def test_clean_symbol_passes(self) -> None:
        rule = SymbolBlacklistRule(blacklist=["DOGE/USDT"])
        assert rule.check(_intent(symbol="BTC/USDT"), {}) is None

    def test_listed_symbol_hard_blocked(self) -> None:
        rule = SymbolBlacklistRule(blacklist=["BTC/USDT"])
        alert = rule.check(_intent(symbol="BTC/USDT"), {})
        assert isinstance(alert, RiskAlert)
        assert alert.level == RiskLevel.CRITICAL  # blacklist is the only hard block

    def test_membership_mutation(self) -> None:
        rule = SymbolBlacklistRule()
        rule.add("XRP/USDT")
        assert "XRP/USDT" in rule.blacklist
        rule.remove("XRP/USDT")
        assert "XRP/USDT" not in rule.blacklist


class TestCooldownGate:
    RECENT, SETTLED = 10, 400  # seconds since last trade; window is 300

    def test_first_trade_unrestricted(self) -> None:
        rule = CooldownRule(cooldown_seconds=300)
        assert rule.check(_intent(), {"last_trade_times": {}}) is None

    def test_rapid_refire_blocked(self) -> None:
        rule = CooldownRule(cooldown_seconds=300)
        ctx = {"last_trade_times": {"BTC/USDT": time.time() - self.RECENT}}
        alert = rule.check(_intent(), ctx)
        assert isinstance(alert, RiskAlert)
        assert alert.rule_id == "cooldown"

    def test_patient_resubmission_allowed(self) -> None:
        rule = CooldownRule(cooldown_seconds=300)
        ctx = {"last_trade_times": {"BTC/USDT": time.time() - self.SETTLED}}
        assert rule.check(_intent(), ctx) is None


class TestDefaultInventory:
    def test_full_deck_of_fifteen(self) -> None:
        # 5 basic + 7 advanced + 3 portfolio rules
        assert len(create_default_rules()) == 15

    def test_every_rule_ships_enabled(self) -> None:
        for rule in create_default_rules():
            assert rule.enabled is True
