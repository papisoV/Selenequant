"""Tests for Phase 0+1 risk hardening improvements.

Verifies: DrawdownCircuitBreaker returns CRITICAL, DailyLossFuse returns
CRITICAL, Kill Switch auto-triggers on CRITICAL rules.
"""

import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in [
    _PROJECT_ROOT / "packages" / "core",
    _PROJECT_ROOT / "packages" / "shared-types",
    _PROJECT_ROOT / "packages" / "risk-engine",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selene_space_risk.advanced_rules import DailyLossFuseRule, DrawdownCircuitBreakerRule
from selene_space_risk.engine import RiskEngine
from selene_space_risk.kill_switch import KillSwitch
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel


def _make_intent(**overrides):
    defaults = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "quantity": 1.0,
        "price": 100.0,
        "order_type": "market",
        "risk_params": {},
        "reasoning": "test",
        "confidence": 0.5,
        "timestamp": 0,
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


class TestDrawdownCritical:
    """DrawdownCircuitBreaker should return CRITICAL, not DANGEROUS."""

    def test_returns_critical_on_breach(self):
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.30)
        ctx = {"total_equity": 7000, "peak_equity": 10000}
        alert = rule.check(_make_intent(), ctx)
        assert alert is not None
        assert alert.level == RiskLevel.CRITICAL

    def test_no_alert_below_threshold(self):
        rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.30)
        ctx = {"total_equity": 9500, "peak_equity": 10000}
        alert = rule.check(_make_intent(), ctx)
        assert alert is None


class TestDailyLossCritical:
    """DailyLossFuse should return CRITICAL, not DANGEROUS."""

    def test_returns_critical_on_breach(self):
        rule = DailyLossFuseRule(daily_loss_pct=0.01)
        ctx = {"total_equity": 10000, "daily_realized_pnl": -200}
        alert = rule.check(_make_intent(), ctx)
        assert alert is not None
        assert alert.level == RiskLevel.CRITICAL

    def test_no_alert_below_threshold(self):
        rule = DailyLossFuseRule(daily_loss_pct=0.01)
        ctx = {"total_equity": 10000, "daily_realized_pnl": -50}
        alert = rule.check(_make_intent(), ctx)
        assert alert is None


class TestKillSwitchAutoTrigger:
    """Kill Switch should auto-activate when CRITICAL rules fire."""

    def test_auto_activate_sets_active(self, tmp_path):
        ks = KillSwitch.__new__(KillSwitch)
        ks._init(tmp_path / "ks_test.json")
        ks.deactivate()
        assert not ks.is_active
        ks.auto_activate(reason="drawdown breach", triggered_by_rule="drawdown_circuit_breaker")
        assert ks.is_active
        assert "Auto:" in ks.reason

    def test_auto_activate_idempotent(self, tmp_path):
        ks = KillSwitch.__new__(KillSwitch)
        ks._init(tmp_path / "ks_test2.json")
        ks.activate(reason="manual")
        ks.auto_activate(reason="should not overwrite", triggered_by_rule="test")
        assert "manual" in ks.reason

    def test_risk_engine_auto_triggers_kill_switch(self, tmp_path):
        ks = KillSwitch.__new__(KillSwitch)
        ks._init(tmp_path / "ks_test3.json")
        ks.deactivate()
        engine = RiskEngine(rules=[DrawdownCircuitBreakerRule()], kill_switch=ks)
        ctx = {"total_equity": 7000, "peak_equity": 10000}
        decision = engine.pre_check(_make_intent(), ctx)
        assert not decision.allowed
        assert ks.is_active
        ks.deactivate()

    def test_no_auto_trigger_when_no_critical(self, tmp_path):
        ks = KillSwitch.__new__(KillSwitch)
        ks._init(tmp_path / "ks_test4.json")
        ks.deactivate()
        engine = RiskEngine(rules=[DrawdownCircuitBreakerRule()], kill_switch=ks)
        ctx = {"total_equity": 9500, "peak_equity": 10000}
        decision = engine.pre_check(_make_intent(), ctx)
        assert decision.allowed
        assert not ks.is_active
