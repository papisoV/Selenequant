"""Regression tests for the market-event → WebSocket bridge guards.

2026-10-13: the large_trade / large_order / liquidation / liquidation_stats /
funding_rate handlers in ``_bridge_market_events`` did ``model_dump(mode="json")``
**and** ``asyncio.ensure_future()`` on the event loop for *every* incoming
market event, unconditionally — with no ``active_count == 0`` short-circuit.
With no WebSocket subscriber those events arrive at ~hundreds/s and the loop
was fully occupied creating Tasks and serialising payloads nobody read,
starving HTTP endpoints into seconds of tail latency (measured: /health
median 900ms on the unguarded tree vs 5ms guarded).

These tests lock the fix:
  * no subscriber  → the handler must NOT serialise and must NOT broadcast;
  * one subscriber → the handler serialises exactly once and broadcasts.

They drive the real ``_bridge_market_events`` wiring (not a re-implementation),
using a duck-typed spy event so ``model_dump`` calls are observable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.v1 import ws as ws_module

# Bridge handlers under test, keyed by the service method the bridge calls.
# Each value is the label the handler broadcasts under.
# (liquidation_stats is the odd one out: it broadcasts under symbol "ALL".)
_GUARDED_HANDLERS = {
    "on_large_trade": "large_trade",
    "on_large_order": "large_order",
    "on_liquidation": "liquidation",
    "on_liquidation_stats": "liquidation_stats",
    "on_funding_rate": "funding_rate",
}


class _SpyEvent:
    """Minimal stand-in for a market event model.

    Only ``.symbol`` and ``.model_dump()`` are touched by the bridge, so a
    duck-typed object is enough — and it lets us count serialisation calls,
    which is the exact work the guard is meant to skip.
    """

    def __init__(self, symbol: str = "BTC/USDT") -> None:
        self.symbol = symbol
        self.dump_calls = 0

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        self.dump_calls += 1
        return {"symbol": self.symbol}


class _FakeMarketService:
    """Records each ``on_*`` callback the bridge registers.

    Any ``on_<name>`` attribute is accepted rather than a hard-coded list: the
    bridge's wiring grows over time (``on_anomaly`` arrived with the A-share
    anomaly work), and a fixed list would make every unrelated handler addition
    fail these tests for the wrong reason. The methods exercised below are the
    guarded ones; what the real service must expose is asserted against the real
    ``MarketDataService`` in test_astock_anomaly_bridge.py.
    """

    def __init__(self) -> None:
        self.callbacks: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        if not name.startswith("on_"):
            raise AttributeError(name)

        def _register(cb: Any, _key: str = name) -> None:
            self.callbacks[_key] = cb

        return _register


@pytest.fixture(autouse=True)
def _fresh_market_manager():
    """Swap in a pristine market ConnectionManager (no leaked subscribers)."""
    original = ws_module._market_manager
    ws_module._market_manager = ws_module.ConnectionManager()
    try:
        yield ws_module._market_manager
    finally:
        ws_module._market_manager = original


def _install_bridge(monkeypatch: pytest.MonkeyPatch):
    """Wire the bridge onto a fake service, capturing broadcast calls."""
    broadcasts: list[tuple[str, str, dict[str, Any]]] = []

    async def _fake_broadcast(
        symbol: str, event_type: str, data: dict[str, Any]
    ) -> None:
        broadcasts.append((event_type, symbol, data))

    # ``_bridge_market_events`` does ``from app.api.v1.ws import broadcast_market_event``
    # at call time, so patching the module attribute before calling it is picked up.
    monkeypatch.setattr(ws_module, "broadcast_market_event", _fake_broadcast)

    from app.main import _bridge_market_events

    svc = _FakeMarketService()
    _bridge_market_events(svc)
    return svc, broadcasts


def _add_dummy_subscriber(manager: Any) -> None:
    """Register one connection so ``active_count`` becomes non-zero.

    The broadcast itself is stubbed in these tests, so the connection object
    never needs to be a real WebSocket.
    """
    manager._connections[object()] = set()


@pytest.mark.asyncio
@pytest.mark.parametrize("method, label", sorted(_GUARDED_HANDLERS.items()))
async def test_no_subscriber_skips_serialisation_and_broadcast(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    label: str,
) -> None:
    """With zero WS subscribers the handler must be a no-op.

    This is the regression: the handler used to serialise + create a Task for
    every event even with nobody listening, starving the event loop.
    """
    svc, broadcasts = _install_bridge(monkeypatch)
    assert ws_module._market_manager.active_count == 0

    event = _SpyEvent("BTC/USDT")
    svc.callbacks[method](event)

    # Give any wrongly-scheduled task a chance to run before asserting.
    await asyncio.sleep(0.02)

    assert event.dump_calls == 0, "serialised an event with no subscriber"
    assert broadcasts == [], f"{label} broadcast despite zero subscribers"


@pytest.mark.asyncio
@pytest.mark.parametrize("method, label", sorted(_GUARDED_HANDLERS.items()))
async def test_with_subscriber_serialises_once_and_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    label: str,
) -> None:
    """With a subscriber the event is serialised exactly once and broadcast."""
    svc, broadcasts = _install_bridge(monkeypatch)
    _add_dummy_subscriber(ws_module._market_manager)
    assert ws_module._market_manager.active_count == 1

    event = _SpyEvent("BTC/USDT")
    svc.callbacks[method](event)

    await asyncio.sleep(0.02)

    expected_symbol = "ALL" if label == "liquidation_stats" else "BTC/USDT"
    assert event.dump_calls == 1, "expected exactly one serialisation"
    assert len(broadcasts) == 1, f"{label} not broadcast to the subscriber"
    event_type, symbol, data = broadcasts[0]
    assert event_type == label
    assert symbol == expected_symbol
    assert data == {"symbol": event.symbol}
