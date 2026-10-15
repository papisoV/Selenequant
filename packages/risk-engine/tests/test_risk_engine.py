"""Tests for risk engine core — advisory alert model."""

from __future__ import annotations

import time
from typing import Any

from selene_space_risk.engine import RiskEngine
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskAlertConfig, RiskLevel
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


def _now_ms() -> int:
    return int(time.time() * 1000)


class _PassRule:
    rule_id = "pass_all"
    enabled = True

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        return None


class _WarningRule:
    rule_id = "warning_rule"
    enabled = True

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        return RiskAlert(
            rule_id=self.rule_id,
            level=RiskLevel.WARNING,
            message="Warning from test rule",
            context={},
            timestamp=_now_ms(),
        )


class _CriticalRule:
    rule_id = "critical_rule"
    enabled = True

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        return RiskAlert(
            rule_id=self.rule_id,
            level=RiskLevel.CRITICAL,
            message="Critical block from test rule",
            context={},
            timestamp=_now_ms(),
        )


class _DisabledRule:
    rule_id = "disabled"
    enabled = False

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        return RiskAlert(
            rule_id=self.rule_id,
            level=RiskLevel.CRITICAL,
            message="Should not run",
            context={},
            timestamp=_now_ms(),
        )


class TestRiskEngine:
    def test_no_rules_allows(self) -> None:
        engine = RiskEngine()
        result = engine.pre_check(_make_intent())
        assert result.allowed is True
        assert result.level == RiskLevel.INFO
        assert len(result.alerts) == 0

    def test_all_rules_pass(self) -> None:
        engine = RiskEngine([_PassRule(), _PassRule()])
        result = engine.pre_check(_make_intent())
        assert result.allowed is True
        assert len(result.alerts) == 0

    def test_warning_allows_with_alert(self) -> None:
        engine = RiskEngine([_WarningRule()])
        result = engine.pre_check(_make_intent())
        assert result.allowed is True
        assert len(result.alerts) == 1
        assert result.alerts[0].level == RiskLevel.WARNING
        assert result.level == RiskLevel.WARNING

    def test_critical_blocks(self) -> None:
        engine = RiskEngine([_CriticalRule()])
        result = engine.pre_check(_make_intent())
        assert result.allowed is False
        assert len(result.alerts) == 1
        assert result.alerts[0].level == RiskLevel.CRITICAL
        assert result.level == RiskLevel.CRITICAL

    def test_disabled_rule_skipped(self) -> None:
        engine = RiskEngine([_DisabledRule()])
        result = engine.pre_check(_make_intent())
        assert result.allowed is True
        assert len(result.alerts) == 0

    def test_add_remove_rule(self) -> None:
        engine = RiskEngine()
        engine.add_rule(_WarningRule())
        assert len(engine.rules) == 1
        engine.remove_rule("warning_rule")
        assert len(engine.rules) == 0

    def test_multiple_alerts_aggregated(self) -> None:
        engine = RiskEngine([_WarningRule(), _CriticalRule()])
        result = engine.pre_check(_make_intent())
        assert result.allowed is False
        assert len(result.alerts) == 2
        assert result.level == RiskLevel.CRITICAL

    def test_worst_level_propagated(self) -> None:
        class RestrictedRule:
            rule_id = "restricted"
            enabled = True

            def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
                return RiskAlert(
                    rule_id=self.rule_id,
                    level=RiskLevel.RESTRICTED,
                    message="restricted",
                    context={},
                    timestamp=_now_ms(),
                )

        engine = RiskEngine([RestrictedRule(), _WarningRule()])
        result = engine.pre_check(_make_intent())
        assert result.allowed is True
        assert result.level == RiskLevel.RESTRICTED

    def test_alert_config_filters_by_min_level(self) -> None:
        config = RiskAlertConfig(enabled=True, min_level=RiskLevel.RESTRICTED)
        engine = RiskEngine([_WarningRule()], alert_config=config)
        result = engine.pre_check(_make_intent())
        assert result.allowed is True
        # WARNING < RESTRICTED, so filtered out
        assert len(result.alerts) == 0

    def test_alert_config_disabled_hides_all_but_critical_still_blocks(self) -> None:
        config = RiskAlertConfig(enabled=False)
        engine = RiskEngine([_WarningRule(), _CriticalRule()], alert_config=config)
        result = engine.pre_check(_make_intent())
        # CRITICAL always hard-blocks regardless of display settings
        assert result.allowed is False
        # But alerts are hidden from display
        assert len(result.alerts) == 0

    def test_alert_config_per_rule_override(self) -> None:
        config = RiskAlertConfig(
            enabled=True,
            min_level=RiskLevel.INFO,
            rules={"warning_rule": {"enabled": False}},
        )
        engine = RiskEngine([_WarningRule(), _CriticalRule()], alert_config=config)
        result = engine.pre_check(_make_intent())
        # warning_rule disabled by per-rule override, critical_rule still active
        assert result.allowed is False
        assert len(result.alerts) == 1
        assert result.alerts[0].rule_id == "critical_rule"
