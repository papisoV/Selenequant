"""Step-3a coverage: AnomalyEvent reaches the market WebSocket bridge.

The detector (S3a) publishes ``AnomalyEvent`` onto the shared bus, but until
this step nothing subscribed: ``_bridge_market_events`` wired eight event
types and never anomalies, so a limit-up was emitted into the void. These
checks pin that wire end-to-end at the seam the frontend actually depends on:

* ``MarketDataService`` exposes ``on_anomaly`` (generated bus listener);
* publishing an ``AnomalyEvent`` fans out to it;
* ``broadcast_market_event`` mirrors an ``anomaly`` onto the ``...:ALL``
  channel, which is the only channel the feed bar subscribes to;
* the channel segment is the event's own ``market_type`` ("intraday"), not the
  bridge's "futures" default — getting this wrong silently misroutes.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.api.v1 import ws as ws_module
from selene_space_market.service import MarketDataService
from selene_space_types.market import AnomalyEvent


def _anomaly(**overrides: Any) -> AnomalyEvent:
    base: dict[str, Any] = {
        "exchange": "astock",
        "symbol": "600519.SH",
        "timestamp": 1_760_000_000_000,
        "kind": "limit_up",
        "price": 11.0,
        "prev_close": 10.0,
        "name": "贵州茅台",
    }
    base.update(overrides)
    return AnomalyEvent(**base)


class TestBusListener:
    def test_service_exposes_on_anomaly(self) -> None:
        svc = MarketDataService()
        assert callable(svc.on_anomaly)

    def test_published_anomaly_reaches_listener(self) -> None:
        svc = MarketDataService()
        received: list[AnomalyEvent] = []
        svc.on_anomaly(received.append)

        event = _anomaly()
        svc.event_bus.publish(event)

        assert received == [event]


class TestBroadcastRouting:
    """``broadcast_market_event`` must reach the ALL channel the feed uses."""

    @pytest.fixture(autouse=True)
    def _fresh_manager(self):
        ws_module._market_manager = ws_module.ConnectionManager()
        yield

    @pytest.mark.asyncio
    async def test_anomaly_mirrors_to_all_channel(self, monkeypatch) -> None:
        sent: list[tuple[str, dict[str, Any]]] = []

        async def _capture(channel: str, payload: dict[str, Any]) -> None:
            sent.append((channel, payload))

        monkeypatch.setattr(ws_module._market_manager, "broadcast", _capture)

        event = _anomaly()
        await ws_module.broadcast_market_event(
            event.symbol, "anomaly", event.model_dump(mode="json")
        )

        channels = [c for c, _ in sent]
        # Per-symbol channel plus the ALL fan-out the feed bar subscribes to.
        assert f"market:astock:intraday:{event.symbol}" in channels
        assert "market:astock:intraday:ALL" in channels

    @pytest.mark.asyncio
    async def test_channel_uses_event_market_type_not_bridge_default(
        self, monkeypatch
    ) -> None:
        sent: list[tuple[str, dict[str, Any]]] = []

        async def _capture(channel: str, payload: dict[str, Any]) -> None:
            sent.append((channel, payload))

        monkeypatch.setattr(ws_module._market_manager, "broadcast", _capture)

        event = _anomaly()
        await ws_module.broadcast_market_event(
            event.symbol, "anomaly", event.model_dump(mode="json")
        )

        # A missing market_type would land on "futures" and never match.
        assert all(":intraday:" in c for c, _ in sent), sent
        assert not any(":futures:" in c for c, _ in sent), sent
