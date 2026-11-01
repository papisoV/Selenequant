"""P1-1: pre-trade risk gate hard-wired into LiveExecutionEngine.place_order.

Verifies:
1. When KillSwitch.is_active is True, place_order:
   - never calls TradingClient.place_order (REST never sent)
   - writes journal intent + mark_failed("kill_switch_active: ...")
   - raises PreTradeRiskBlockError

2. When RiskEngine.pre_check returns decision.allowed=False (CRITICAL),
   place_order:
   - never calls TradingClient.place_order
   - writes journal intent + mark_failed("risk_blocked: ...")
   - raises PreTradeRiskBlockError

3. When risk gate passes (no kill_switch, risk_engine returns allowed=True),
   place_order proceeds normally to REST.

4. When no risk_engine / kill_switch injected (backward compat), place_order
   proceeds normally — no gate applied.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.exceptions import PreTradeRiskBlockError
from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockJournal:
    def __init__(self) -> None:
        self.intents: list[dict] = []
        self.failed: list[tuple[str, str]] = []
        self.submitted: list[tuple[str, str]] = []
        self.confirmed: list[str] = []

    def append_intent(self, **kwargs) -> None:
        self.intents.append(kwargs)

    def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        self.failed.append((client_order_id, reason))

    def mark_submitted(self, client_order_id: str, exchange_order_id: str) -> None:
        self.submitted.append((client_order_id, exchange_order_id))

    def mark_confirmed(self, client_order_id: str) -> None:
        self.confirmed.append(client_order_id)


class _MockKillSwitch:
    def __init__(self, active: bool = False, reason: str = "") -> None:
        self._active = active
        self._reason = reason

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def reason(self) -> str:
        return self._reason

    def is_active_for(
        self,
        *,
        account_id: str | None = None,
        strategy_id: str | None = None,
    ) -> bool:
        """Scoped query — for the legacy mock, mirrors the SYSTEM scope."""
        return self._active


class _MockRiskEngine:
    def __init__(self, allowed: bool = True, level: str = "info", reason: str = "") -> None:
        self._allowed = allowed
        self._level = SimpleNamespace(value=level)
        self._reason = reason
        self.pre_check_calls: list = []

    def pre_check(self, intent, context=None):
        self.pre_check_calls.append((intent, context))
        return SimpleNamespace(
            allowed=self._allowed,
            level=self._level,
            reason=self._reason,
            alerts=[],
            timestamp=0,
        )


def _make_engine(
    *,
    journal: _MockJournal | None = None,
    risk_engine: _MockRiskEngine | None = None,
    kill_switch: _MockKillSwitch | None = None,
    client_return_value=None,
) -> tuple[LiveExecutionEngine, AsyncMock, _MockJournal]:
    """Build a LiveExecutionEngine with mocked client + journal."""
    journal = journal or _MockJournal()
    client = SimpleNamespace(
        exchange_name="test",
        place_order=AsyncMock(return_value=client_return_value),
    )
    engine = LiveExecutionEngine(
        trading_client=client,
        journal=journal,
        state_repo=None,
        risk_engine=risk_engine,
        kill_switch=kill_switch,
    )
    return engine, client, journal


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocks_on_kill_switch_active() -> None:
    """Kill switch active → PreTradeRiskBlockError, no REST, journal marked failed."""
    engine, client, journal = _make_engine(
        kill_switch=_MockKillSwitch(active=True, reason="manual"),
    )

    with pytest.raises(PreTradeRiskBlockError) as exc_info:
        await engine.place_order(
            account_id="acct-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
        )

    assert "kill switch" in str(exc_info.value).lower()
    # REST never called.
    client.place_order.assert_not_awaited()
    # Journal recorded the rejected intent + mark_failed.
    assert len(journal.intents) == 1
    assert journal.intents[0]["client_order_id"].startswith("selenespace-")
    assert len(journal.failed) == 1
    assert "kill_switch_active" in journal.failed[0][1]
    assert "manual" in journal.failed[0][1]


@pytest.mark.asyncio
async def test_blocks_on_critical_risk() -> None:
    """RiskEngine returns allowed=False → PreTradeRiskBlockError, no REST."""
    risk = _MockRiskEngine(allowed=False, level="critical", reason="position limit breached")
    engine, client, journal = _make_engine(risk_engine=risk)

    with pytest.raises(PreTradeRiskBlockError) as exc_info:
        await engine.place_order(
            account_id="acct-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=50_000.0,
        )

    assert "risk engine" in str(exc_info.value).lower()
    assert "position limit breached" in str(exc_info.value)
    client.place_order.assert_not_awaited()
    # Risk engine was actually called.
    assert len(risk.pre_check_calls) == 1
    # Journal recorded the rejected intent.
    assert len(journal.failed) == 1
    assert "risk_blocked" in journal.failed[0][1]
    assert "position limit breached" in journal.failed[0][1]


@pytest.mark.asyncio
async def test_passes_when_risk_allows() -> None:
    """RiskEngine returns allowed=True → place_order proceeds to REST."""
    risk = _MockRiskEngine(allowed=True, level="info", reason="")
    response = SimpleNamespace(
        order_id="ex-1",
        status=OrderStatus.ACCEPTED,
        filled_quantity=0.0,
        avg_fill_price=0.0,
    )
    engine, client, journal = _make_engine(
        risk_engine=risk,
        client_return_value=response,
    )

    order = await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.1,
    )

    # REST was called.
    client.place_order.assert_awaited_once()
    # No mark_failed (order succeeded).
    assert journal.failed == []
    # Journal intent was written (the happy path writes it after the gate).
    assert len(journal.intents) == 1
    assert order.symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_no_gate_when_no_risk_engine_no_kill_switch() -> None:
    """Backward compat: no risk_engine / kill_switch → no gate, proceeds to REST."""
    response = SimpleNamespace(
        order_id="ex-2",
        status=OrderStatus.ACCEPTED,
        filled_quantity=0.0,
        avg_fill_price=0.0,
    )
    engine, client, journal = _make_engine(client_return_value=response)

    order = await engine.place_order(
        account_id="acct-1",
        symbol="ETHUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=1.0,
    )

    client.place_order.assert_awaited_once()
    assert journal.failed == []
    assert order.symbol == "ETHUSDT"


@pytest.mark.asyncio
async def test_kill_switch_takes_precedence_over_risk_engine() -> None:
    """If both kill_switch active AND risk would block, kill_switch wins."""
    risk = _MockRiskEngine(allowed=False, level="critical", reason="would block")
    engine, client, journal = _make_engine(
        kill_switch=_MockKillSwitch(active=True, reason="emergency"),
        risk_engine=risk,
    )

    with pytest.raises(PreTradeRiskBlockError) as exc_info:
        await engine.place_order(
            account_id="acct-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
        )

    # Kill switch was checked first — risk engine never called.
    assert "kill switch" in str(exc_info.value).lower()
    assert len(risk.pre_check_calls) == 0
    client.place_order.assert_not_awaited()


def test_set_risk_engine_and_kill_switch_setters() -> None:
    """Setters allow post-construction injection (main.py lifespan pattern)."""
    client = SimpleNamespace(
        exchange_name="test",
        place_order=AsyncMock(),
    )
    engine = LiveExecutionEngine(trading_client=client)

    assert engine._risk_engine is None
    assert engine._kill_switch is None

    risk = _MockRiskEngine()
    ks = _MockKillSwitch()
    engine.set_risk_engine(risk)
    engine.set_kill_switch(ks)

    assert engine._risk_engine is risk
    assert engine._kill_switch is ks
