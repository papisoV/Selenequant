"""Tests for WsHealthMonitor + live_engine place_order WS health gate (Phase 3).

Verifies:
  - WsHealthMonitor.is_healthy() returns False on stale heartbeat (>5s)
  - WsHealthMonitor.is_healthy() returns False on excessive reconnects (>5 in 1h)
  - WsHealthMonitor.is_healthy() returns True when heartbeat fresh + reconnects low
  - reconnect count expires after 1h window
  - place_order raises WsUnhealthyError when monitor reports unhealthy
  - place_order marks journal failed with ws_unhealthy reason
  - place_order proceeds normally when monitor reports healthy
  - place_order skips gate when monitor is None (backward compat)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.execution.live_state import LiveStateRepository
from selene_space_exchange.execution.order_journal import OrderJournal
from selene_space_exchange.execution.ws_health_monitor import (
    WsHealthMonitor,
    WsUnhealthyError,
)
from selene_space_exchange.trading import (
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


class _MockTime:
    """Controllable clock for deterministic WsHealthMonitor tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _make_mock_client() -> TradingClient:
    """Build a TradingClient with AsyncMock place_order (so we can assert_not_awaited)."""
    client = AsyncMock(spec=TradingClient)
    client.exchange_name = "mock"
    client.place_order = AsyncMock(return_value=OrderResponse(
        exchange="mock",
        order_id="ex-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        status=OrderStatus.ACCEPTED,
        quantity=0.01,
    ))
    client.cancel_order = AsyncMock()
    client.get_order = AsyncMock()
    client.get_open_orders = AsyncMock(return_value=[])
    client.get_balances = AsyncMock(return_value=[])
    client.test_connectivity = AsyncMock(return_value=True)
    return client


@pytest_asyncio.fixture
def journal(tmp_path: Path) -> OrderJournal:
    return OrderJournal(journal_dir=tmp_path / "journal")


@pytest_asyncio.fixture
def state_repo(tmp_path: Path) -> LiveStateRepository:
    return LiveStateRepository(state_dir=tmp_path / "live")


# ---------------------------------------------------------------------------
# WsHealthMonitor unit tests
# ---------------------------------------------------------------------------


def test_stale_heartbeat_detected() -> None:
    """record_heartbeat then advance >5s → is_healthy()=False."""
    clock = _MockTime()
    monitor = WsHealthMonitor(now_fn=clock)
    monitor.record_heartbeat()
    assert monitor.is_healthy() is True  # fresh

    clock.advance(6.0)  # 6s > 5s threshold
    assert monitor.is_healthy() is False
    assert monitor.reason is not None
    assert "stale_heartbeat" in monitor.reason


def test_excessive_reconnects_detected() -> None:
    """6 reconnects in 1h → is_healthy()=False (threshold is 5).

    Heartbeat is refreshed between reconnects so we test the reconnect branch,
    not the stale-heartbeat branch.
    """
    clock = _MockTime()
    monitor = WsHealthMonitor(now_fn=clock)
    monitor.record_heartbeat()

    for _ in range(6):
        monitor.record_reconnect()
        clock.advance(1.0)
        monitor.record_heartbeat()  # keep heartbeat fresh to isolate reconnect check

    assert monitor.is_healthy() is False
    assert "excessive_reconnects" in (monitor.reason or "")


def test_reconnect_count_expires_after_1h() -> None:
    """5 reconnects, then advance >1h, then 1 more reconnect → only 1 in window → healthy."""
    clock = _MockTime()
    monitor = WsHealthMonitor(now_fn=clock)
    monitor.record_heartbeat()

    # 5 reconnects (at threshold, still healthy)
    for _ in range(5):
        monitor.record_reconnect()
        clock.advance(1.0)
    assert monitor.is_healthy() is True

    # Advance past 1h window — all previous reconnects expire
    clock.advance(3700.0)
    # Heartbeat is now stale too, refresh it
    monitor.record_heartbeat()
    # 1 reconnect in new window
    monitor.record_reconnect()
    assert monitor.is_healthy() is True, (
        f"expected healthy after window expiry, got reason={monitor.reason}"
    )


def test_healthy_when_fresh_heartbeat_and_low_reconnects() -> None:
    """Sanity: fresh heartbeat + 2 reconnects → healthy."""
    clock = _MockTime()
    monitor = WsHealthMonitor(now_fn=clock)
    monitor.record_heartbeat()
    monitor.record_reconnect()
    monitor.record_reconnect()
    assert monitor.is_healthy() is True
    assert monitor.reason is None


def test_no_heartbeat_yet_is_unhealthy() -> None:
    """Before any heartbeat recorded, monitor should report unhealthy."""
    monitor = WsHealthMonitor()
    assert monitor.is_healthy() is False
    assert monitor.reason == "no_heartbeat_yet"


# ---------------------------------------------------------------------------
# live_engine place_order gate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocks_order_on_stale_heartbeat(
    journal: OrderJournal, state_repo: LiveStateRepository
) -> None:
    """place_order raises WsUnhealthyError when WS heartbeat is stale,
    and records the rejection in the journal. REST never called."""
    clock = _MockTime()
    monitor = WsHealthMonitor(now_fn=clock)
    monitor.record_heartbeat()
    clock.advance(6.0)  # stale
    assert monitor.is_healthy() is False

    client = _make_mock_client()
    # Use a mock journal so we can capture intents + mark_failed calls easily.
    mock_journal = MagicMock(wraps=SimpleNamespace(
        append_intent=lambda **kw: None,
        mark_failed=lambda *a, **kw: None,
        mark_submitted=lambda *a, **kw: None,
    ))
    engine = LiveExecutionEngine(
        client,  # type: ignore[arg-type]
        journal=mock_journal,  # type: ignore[arg-type]
        state_repo=state_repo,
        ws_health_monitor=monitor,
    )

    with pytest.raises(WsUnhealthyError):
        await engine.place_order(
            account_id="pa-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
        )

    # REST never called.
    client.place_order.assert_not_awaited()
    # Journal recorded the rejected intent + mark_failed with ws_unhealthy reason.
    mock_journal.append_intent.assert_called_once()
    mock_journal.mark_failed.assert_called_once()
    failed_kwargs = mock_journal.mark_failed.call_args.kwargs
    failed_reason = failed_kwargs.get("reason", "")
    assert failed_reason.startswith("ws_unhealthy:")
    assert "stale_heartbeat" in failed_reason


@pytest.mark.asyncio
async def test_allows_order_when_healthy(
    journal: OrderJournal, state_repo: LiveStateRepository
) -> None:
    """place_order proceeds to REST when WS is healthy."""
    monitor = WsHealthMonitor()
    monitor.record_heartbeat()  # fresh

    client = _make_mock_client()
    engine = LiveExecutionEngine(
        client,  # type: ignore[arg-type]
        journal=journal,
        state_repo=state_repo,
        ws_health_monitor=monitor,
    )

    order = await engine.place_order(
        account_id="pa-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    assert order is not None
    client.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_gate_when_monitor_none(
    journal: OrderJournal, state_repo: LiveStateRepository
) -> None:
    """When ws_health_monitor=None (backward compat), place_order skips the gate."""
    client = _make_mock_client()
    engine = LiveExecutionEngine(
        client,  # type: ignore[arg-type]
        journal=journal,
        state_repo=state_repo,
        ws_health_monitor=None,
    )

    order = await engine.place_order(
        account_id="pa-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    assert order is not None
    client.place_order.assert_awaited_once()
