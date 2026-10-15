"""Tests for PortfolioMutexRule — cross-strategy mutual exclusion rule.

Covers the rule in isolation plus an integration test through RiskEngine.
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_risk.engine import RiskEngine
from selene_space_risk.portfolio_mutex import PortfolioMutex
from selene_space_risk.portfolio_mutex_rule import PortfolioMutexRule
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
        "risk_params": {},
        "timestamp": int(time.time() * 1000),
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


class TestPortfolioMutexRule:
    def test_no_lock_allows(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex)
        ctx = {"strategy_id": "strategy_a"}
        assert rule.check(_make_intent(), ctx) is None

    def test_same_strategy_allows(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex)
        mutex.acquire("BTC/USDT", "strategy_a")
        ctx = {"strategy_id": "strategy_a"}
        assert rule.check(_make_intent(symbol="BTC/USDT"), ctx) is None

    def test_other_strategy_restricted(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex)
        mutex.acquire("BTC/USDT", "strategy_a")
        ctx = {"strategy_id": "strategy_b"}
        alert = rule.check(_make_intent(symbol="BTC/USDT"), ctx)
        assert isinstance(alert, RiskAlert)
        assert alert.rule_id == "portfolio_mutex"
        assert alert.level == RiskLevel.RESTRICTED
        assert alert.context["owner_strategy"] == "strategy_a"
        assert alert.context["strategy_id"] == "strategy_b"

    def test_missing_strategy_id_allows(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex)
        mutex.acquire("BTC/USDT", "strategy_a")
        # no strategy_id in context → must not hard-fail
        assert rule.check(_make_intent(symbol="BTC/USDT"), {}) is None

    def test_disabled_rule_skipped(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex, enabled=False)
        mutex.acquire("BTC/USDT", "strategy_a")
        ctx = {"strategy_id": "strategy_b"}
        assert rule.check(_make_intent(symbol="BTC/USDT"), ctx) is None

    def test_context_mutex_takes_precedence(self) -> None:
        shared = PortfolioMutex()
        other = PortfolioMutex()
        rule = PortfolioMutexRule(shared)
        other.acquire("BTC/USDT", "strategy_a")
        ctx = {"strategy_id": "strategy_b", "portfolio_mutex": other}
        alert = rule.check(_make_intent(symbol="BTC/USDT"), ctx)
        assert isinstance(alert, RiskAlert)
        assert alert.level == RiskLevel.RESTRICTED


class TestIntegrationWithEngine:
    def test_second_strategy_restricted_through_engine(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex)
        engine = RiskEngine([rule])

        # Strategy A acquires the lock (order filled in live engine).
        mutex.acquire("BTC/USDT", "strategy_a")

        # Strategy A re-opens → allowed, no alert.
        first = engine.pre_check(
            _make_intent(symbol="BTC/USDT"), {"strategy_id": "strategy_a"}
        )
        assert first.allowed is True
        assert first.alerts == []

        # Strategy B tries the same symbol → RESTRICTED alert, still allowed
        # (advisory), surfaced to the UI.
        second = engine.pre_check(
            _make_intent(symbol="BTC/USDT"), {"strategy_id": "strategy_b"}
        )
        assert second.allowed is True
        assert len(second.alerts) == 1
        assert second.alerts[0].rule_id == "portfolio_mutex"
        assert second.level == RiskLevel.RESTRICTED

    def test_engine_allows_after_release(self) -> None:
        mutex = PortfolioMutex()
        rule = PortfolioMutexRule(mutex)
        engine = RiskEngine([rule])

        mutex.acquire("BTC/USDT", "strategy_a")
        mutex.release("BTC/USDT", "strategy_a")

        result = engine.pre_check(
            _make_intent(symbol="BTC/USDT"), {"strategy_id": "strategy_b"}
        )
        assert result.allowed is True
        assert result.alerts == []
