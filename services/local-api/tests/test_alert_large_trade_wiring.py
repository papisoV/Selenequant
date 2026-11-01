"""The large-trade bridge must be wired to the *market service's* bus.

The unit tests drive ``handle_event`` directly, so they would all pass with the
bridge attached to the wrong bus (or to none). These pin the seam that decides
whether a user's rule ever sees a trade:

* a real ``MarketDataService`` promotes a qualifying print through its own
  ingestion path and the running consumer delivers it;
* a print under the detector's floor never reaches the rule at all;
* the module-level singleton bus is *not* the service bus — attaching there
  looks right and receives nothing (``service.py:114``).
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from app.core.alert_large_trade import LargeTradeAlertBridge
from selene_space_core.events.bus import get_event_bus
from selene_space_market.service import MarketDataService
from selene_space_types.market import TradeEvent


def _rule(minimum: float = 50_000) -> dict:
    return {
        "id": "lt1",
        "name": "鲸鱼",
        "type": "large_trade",
        "threshold": {"min_notional_usd": minimum},
    }


def _trade(price: float = 50_000.0, quantity: float = 2.0) -> TradeEvent:
    return TradeEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=1_760_000_000_000,
        trade_id="t1",
        price=price,
        quantity=quantity,
        market_type="futures",
        side="buy",
    )


def _bridge(sent: list[str], minimum: float = 50_000) -> LargeTradeAlertBridge:
    return LargeTradeAlertBridge(
        load_rules=lambda: [_rule(minimum)],
        notify=sent.append,
        loop=asyncio.get_running_loop(),
    )


@pytest_asyncio.fixture
async def consumer():
    """Run a bridge's consumer task for the duration of the test.

    The bus handler only enqueues (it runs on the ingestion path), so without
    this nothing would ever be evaluated and every assertion below would be
    vacuously green.
    """
    sent: list[str] = []

    def _start(bridge: LargeTradeAlertBridge) -> asyncio.Task:
        return asyncio.create_task(bridge.run(), name="alert-large-trade-test")

    tasks: list[asyncio.Task] = []

    def _spawn(bridge: LargeTradeAlertBridge) -> asyncio.Task:
        task = _start(bridge)
        tasks.append(task)
        return task

    yield sent, _spawn

    for task in tasks:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def _drain(sent: list[str], n: int = 1, timeout: float = 1.0) -> None:
    """Wait until *n* notifications arrived, or fail the timeout."""
    for _ in range(int(timeout / 0.005)):
        if len(sent) >= n:
            return
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_trade_ingestion_reaches_the_bridge(consumer):
    sent, spawn = consumer
    svc = MarketDataService(large_trade_threshold_usd=10_000.0)
    bridge = _bridge(sent)
    spawn(bridge)
    svc.on_large_trade(bridge.on_event)

    svc._on_trade_event(_trade())  # the real ingestion entry point
    await _drain(sent)

    assert len(sent) == 1
    assert "BTC/USDT" in sent[0]


@pytest.mark.asyncio
async def test_a_print_below_the_detector_floor_never_reaches_the_bridge(consumer):
    """The detector's floor (main.py sets 10k) drops small prints before the
    rule threshold is consulted — a rule set to 1 USD must not turn every
    retail print into a page."""
    sent, spawn = consumer
    svc = MarketDataService(large_trade_threshold_usd=10_000.0)
    bridge = _bridge(sent, minimum=1)
    spawn(bridge)
    svc.on_large_trade(bridge.on_event)

    svc._on_trade_event(_trade(price=100.0, quantity=1.0))  # 100 USD
    await asyncio.sleep(0.1)

    assert sent == []


@pytest.mark.asyncio
async def test_a_qualifying_print_below_the_rule_threshold_does_not_fire(consumer):
    """Passing the detector floor is not the same as passing the user's rule."""
    sent, spawn = consumer
    svc = MarketDataService(large_trade_threshold_usd=10_000.0)
    bridge = _bridge(sent, minimum=500_000)
    spawn(bridge)
    svc.on_large_trade(bridge.on_event)

    svc._on_trade_event(_trade(price=50_000.0, quantity=2.0))  # 100k < 500k
    await asyncio.sleep(0.1)

    assert sent == []


@pytest.mark.asyncio
async def test_the_global_singleton_bus_is_not_the_service_bus(consumer):
    """Guards the failure mode this wiring exists to avoid: attaching to
    ``get_event_bus()`` looks right and receives nothing."""
    sent, spawn = consumer
    svc = MarketDataService(large_trade_threshold_usd=10_000.0)
    bridge = _bridge(sent)
    spawn(bridge)
    wrong_bus = get_event_bus()
    wrong_bus.subscribe(TradeEvent, bridge.on_event)
    try:
        svc._on_trade_event(_trade())
        await asyncio.sleep(0.1)
        assert sent == []
    finally:
        wrong_bus.unsubscribe(TradeEvent, bridge.on_event)


@pytest.mark.asyncio
async def test_unsubscribing_stops_delivery(consumer):
    """After teardown the detector no longer hands anything to the bridge."""
    sent, spawn = consumer
    svc = MarketDataService(large_trade_threshold_usd=10_000.0)
    bridge = _bridge(sent)
    spawn(bridge)
    svc.on_large_trade(bridge.on_event)

    svc._on_trade_event(_trade())
    await _drain(sent)
    assert len(sent) == 1

    from selene_space_types.derivatives import LargeTradeEvent

    svc.event_bus.unsubscribe(LargeTradeEvent, bridge.on_event)
    svc._on_trade_event(_trade())
    await asyncio.sleep(0.1)
    assert len(sent) == 1
