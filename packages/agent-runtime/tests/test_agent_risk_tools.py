"""Behaviour locks for risk tools: RiskCheckTool verdicts and RiskReportTool
rule listing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from selene_space_agent.tools.risk_tools import RiskCheckTool, RiskReportTool
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskDecision, RiskLevel


@dataclass
class _StubRule:
    rule_id: str = "position_cap"
    enabled: bool = True

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskDecision:
        return RiskDecision(
            rule_id=self.rule_id,
            allowed=True,
            level=RiskLevel.SAFE,
            reason="Within limits",
            timestamp=1_710_000_000_000,
        )


@dataclass
class _StubEngine:
    """pre_check stand-in; `allow` flips the verdict."""

    _rules: list[_StubRule]
    _allow: bool = True

    def __init__(self, rules: list[_StubRule] | None = None, allow: bool = True) -> None:
        self._rules = [_StubRule()] if rules is None else rules
        self._allow = allow

    @property
    def rules(self) -> list[_StubRule]:
        return self._rules

    def pre_check(self, intent: TradeIntent, context: dict[str, Any] | None = None) -> RiskDecision:
        if self._allow:
            return RiskDecision(
                rule_id="aggregate",
                allowed=True,
                level=RiskLevel.SAFE,
                reason="All checks passed",
                timestamp=1_710_000_000_000,
            )
        return RiskDecision(
            rule_id="position_cap",
            allowed=False,
            level=RiskLevel.BLOCKED,
            reason="Order notional above cap",
            timestamp=1_710_000_000_000,
        )


_INTENT = {
    "symbol": "ETH/USDT",
    "side": "buy",
    "quantity": 3.0,
    "order_type": "market",
    "reasoning": "breakout confirmation",
    "confidence": 0.72,
    "risk_params": {"stop_loss": 0.03},
    "timestamp": 1_710_000_000_000,
}


class TestRiskCheckTool:
    def test_passing_verdict_rendered(self) -> None:
        out = RiskCheckTool(_StubEngine(allow=True)).execute({"intent": _INTENT})
        checks = (out.error is None, "ALLOWED" in out.output)
        assert all(checks)

    def test_blocking_verdict_includes_reason(self) -> None:
        out = RiskCheckTool(_StubEngine(allow=False)).execute({"intent": _INTENT})
        checks = (out.error is None, "BLOCKED" in out.output, "Order notional above cap" in out.output)
        assert all(checks)

    def test_garbage_intent_rejected(self) -> None:
        out = RiskCheckTool(_StubEngine()).execute({"intent": {"nonsense": True}})
        assert out.error is not None

    def test_intent_key_required(self) -> None:
        assert RiskCheckTool(_StubEngine()).execute({}).error is not None


class TestRiskReportTool:
    def test_rule_rows_show_state_labels(self) -> None:
        rules = [_StubRule("alpha_rule", True), _StubRule("beta_rule", False)]
        out = RiskReportTool(_StubEngine(rules=rules)).execute({})
        quad = ("alpha_rule" in out.output, "beta_rule" in out.output,
                "enabled" in out.output, "disabled" in out.output)
        assert all(quad)

    def test_empty_engine_reports_no_rules(self) -> None:
        out = RiskReportTool(_StubEngine(rules=[])).execute({})
        assert "No risk rules" in out.output
