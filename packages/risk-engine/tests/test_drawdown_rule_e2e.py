"""E2E RED test: DrawdownCircuitBreakerRule triggers KillSwitch via EquityTracker.

Phase 2a: RED — EquityTracker stub raises NotImplementedError, so peak_equity
cannot be obtained from the tracker, so the rule never sees a drawdown > 0.

Phase 2b will implement EquityTracker.peak → 100, then:
  tracker.update(100.0, ts1)  # peak = 100
  tracker.update(70.0, ts2)   # current = 70, peak stays 100
  context["peak_equity"] = tracker.peak  # 100
  context["total_equity"] = 70.0
  rule.check(intent, context) → RiskAlert(CRITICAL)
  → engine auto-triggers KillSwitch SYSTEM
"""

from __future__ import annotations

from datetime import datetime, timezone

from selene_space_risk.advanced_rules import DrawdownCircuitBreakerRule
from selene_space_risk.equity_tracker import EquityTracker
from selene_space_risk.kill_switch import KillSwitch
from selene_space_risk.engine import RiskEngine
from selene_space_types.agent import TradeIntent


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def test_drawdown_rule_triggers_killswitch_e2e() -> None:
    """30% drawdown (peak=100, current=70) → DrawdownCircuitBreakerRule 20% threshold
    → CRITICAL alert → KillSwitch SYSTEM activated.

    RED: EquityTracker.peak raises NotImplementedError → cannot wire peak_equity=100
    into context → rule sees peak_equity=0 (or total_equity if hardcoded) → no trigger.
    """
    tracker = EquityTracker()
    # Phase 2b: peak=100, current=70 → drawdown=0.30 (exceeds 0.20 threshold)
    tracker.update(100.0, _ts(2026, 7, 26, 9, 0))   # peak = 100
    tracker.update(70.0, _ts(2026, 7, 26, 14, 0))   # current = 70, peak stays 100

    kill_switch = KillSwitch()
    # Reset any singleton state from prior tests — KillSwitch is a singleton.
    kill_switch.deactivate()
    rule = DrawdownCircuitBreakerRule(max_drawdown_pct=0.20)  # 20% threshold
    engine = RiskEngine(
        rules=[rule],
        kill_switch=kill_switch,
    )

    intent = TradeIntent(
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.01,
        reasoning="test intent for drawdown e2e",
        confidence=0.5,
        timestamp=_ts(2026, 7, 26, 14, 1),
    )

    context = {
        "total_equity": 70.0,
        "peak_equity": tracker.peak,  # tracker.peak = 100 (Phase 2b GREEN)
        "portfolio_leverage": 1.0,
        # NOTE: no strategy_id → KillSwitch auto_activate uses SYSTEM scope
        # (legacy behavior). With strategy_id present, engine activates
        # STRATEGY scope, which is_active (SYSTEM) would be False.
    }

    decision = engine.pre_check(intent, context)

    # Expected (Phase 2b GREEN):
    assert decision.allowed is False, "DrawdownRule should BLOCK the intent"
    assert kill_switch.is_active is True, "KillSwitch should be SYSTEM active"
    assert any(a.rule_id == "drawdown_circuit_breaker" for a in decision.alerts)
