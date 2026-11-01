"""C5 — Paper engine risk-engine hook test (parity feature, default OFF).

Asserts that:
- When SELENE_PAPER_RISK_ENFORCE=1 AND risk_engine.pre_check returns
  allowed=False, paper place_order raises OrderRejected.
- When flag is OFF (default), risk check is skipped — order proceeds.
- When risk_engine is None (not injected), no check regardless of flag.
"""

from __future__ import annotations


import pytest

from selene_space_paper.paper_execution import (
    OrderRejected,
    PaperExecutionEngine,
    _paper_risk_enforce_enabled,
)
from selene_space_types.risk import RiskAlert, RiskDecision, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


class _BlockingRiskEngine:
    """Fake risk engine that always blocks (CRITICAL)."""

    def pre_check(self, intent, context=None) -> RiskDecision:
        return RiskDecision(
            allowed=False,
            level=RiskLevel.CRITICAL,
            reason="blacklisted symbol",
            alerts=[
                RiskAlert(
                    rule_id="test_block",
                    level=RiskLevel.CRITICAL,
                    message="blocked",
                    context={},
                    timestamp=int(0),
                )
            ],
            timestamp=int(0),
        )


class _AllowingRiskEngine:
    """Fake risk engine that always allows."""

    def pre_check(self, intent, context=None) -> RiskDecision:
        return RiskDecision(
            allowed=True,
            level=RiskLevel.INFO,
            reason="ok",
            alerts=[],
            timestamp=int(0),
        )


class TestPaperRiskEnforceFlag:
    def test_flag_default_off(self) -> None:
        assert _paper_risk_enforce_enabled() is False

    def test_flag_on_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELENE_PAPER_RISK_ENFORCE", "1")
        assert _paper_risk_enforce_enabled() is True

    def test_flag_off_when_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELENE_PAPER_RISK_ENFORCE", "0")
        assert _paper_risk_enforce_enabled() is False

    def test_flag_truthy_synonyms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", "Yes"):
            monkeypatch.setenv("SELENE_PAPER_RISK_ENFORCE", val)
            assert _paper_risk_enforce_enabled() is True, f"failed for {val!r}"


class TestPaperRiskHook:
    @pytest.mark.asyncio
    async def test_blocked_when_flag_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag ON + blocking risk engine → OrderRejected."""
        monkeypatch.setenv("SELENE_PAPER_RISK_ENFORCE", "1")
        eng = PaperExecutionEngine(
            initial_balance=10_000.0,
            risk_engine=_BlockingRiskEngine(),
        )
        await eng.start()
        try:
            with pytest.raises(OrderRejected, match="blacklisted symbol"):
                await eng.place_order(
                    account_id="paper-default",
                    symbol="BTC/USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=100.0,
                    mark_price=50_000.0,
                    signal_timestamp_ms=1,
                )
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_allowed_when_flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag OFF (default) + blocking risk engine → order proceeds (no check)."""
        monkeypatch.delenv("SELENE_PAPER_RISK_ENFORCE", raising=False)
        eng = PaperExecutionEngine(
            initial_balance=10_000.0,
            risk_engine=_BlockingRiskEngine(),  # would block if checked
        )
        await eng.start()
        try:
            order = await eng.place_order(
                account_id="paper-default",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                mark_price=50_000.0,
                signal_timestamp_ms=1,
            )
            assert order is not None
            # Order should have been placed (not rejected).
            assert order.symbol == "BTC/USDT"
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_allowed_when_risk_engine_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag ON but risk_engine=None → no check, order proceeds."""
        monkeypatch.setenv("SELENE_PAPER_RISK_ENFORCE", "1")
        eng = PaperExecutionEngine(
            initial_balance=10_000.0,
            risk_engine=None,  # no engine injected
        )
        await eng.start()
        try:
            order = await eng.place_order(
                account_id="paper-default",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                mark_price=50_000.0,
                signal_timestamp_ms=1,
            )
            assert order is not None
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_allowed_when_risk_engine_approves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag ON + allowing risk engine → order proceeds."""
        monkeypatch.setenv("SELENE_PAPER_RISK_ENFORCE", "1")
        eng = PaperExecutionEngine(
            initial_balance=10_000.0,
            risk_engine=_AllowingRiskEngine(),
        )
        await eng.start()
        try:
            order = await eng.place_order(
                account_id="paper-default",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                mark_price=50_000.0,
                signal_timestamp_ms=1,
            )
            assert order is not None
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_backward_compat_no_risk_engine_arg(self) -> None:
        """Existing callers that don't pass risk_engine still work (default None)."""
        eng = PaperExecutionEngine(initial_balance=10_000.0)
        assert eng._risk_engine is None
        await eng.start()
        try:
            order = await eng.place_order(
                account_id="paper-default",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                mark_price=50_000.0,
                signal_timestamp_ms=1,
            )
            assert order is not None
        finally:
            await eng.stop()
