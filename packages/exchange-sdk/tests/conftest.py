"""Shared doubles, env gates, and sample wire messages for exchange-sdk tests."""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from typing import Any

import pytest

# Safety net for Phase 5.5-P0.5: place_order() refuses to fire without this.
# Suite-wide default ON; gate-verification tests pop it explicitly.
os.environ.setdefault("SELENE_ENABLE_REAL_TRADING", "1")

_AUTO_APPROVE_MARKER = "no_auto_approve"


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "no_auto_approve: opt a test out of the conftest LiveExecutionEngine "
        "auto-approve fixture (used to verify the approval gate itself)",
    )


@pytest.fixture(autouse=True)
def _auto_approve_live_engine(request: pytest.FixtureRequest):
    """Auto-set ``_first_trade_approved`` on every constructed engine.

    Tests verifying the gate itself mark with @no_auto_approve to see the
    un-patched constructor behavior.
    """
    if request.node.get_closest_marker(_AUTO_APPROVE_MARKER):
        yield
        return

    from selene_space_exchange.execution.live_engine import LiveExecutionEngine

    real_init = LiveExecutionEngine.__init__

    def _approved_init(instance, *args, **kwargs):
        real_init(instance, *args, **kwargs)
        instance._first_trade_approved = True

    LiveExecutionEngine.__init__ = _approved_init  # type: ignore[assignment]
    try:
        yield
    finally:
        LiveExecutionEngine.__init__ = real_init  # type: ignore[assignment]


class MockWSConnection:
    """In-memory WebSocket double speaking the websockets client protocol shape."""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._inbox: deque[str] = deque(json.dumps(m) for m in (messages or ()))
        self._outbox: list[str] = []
        self._closed = False

    @property
    def sent_messages(self) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw in self._outbox]

    def add_message(self, msg: dict[str, Any]) -> None:
        """Queue an extra inbound frame."""
        self._inbox.append(json.dumps(msg))

    async def send(self, data: str) -> None:
        self._outbox.append(data)

    async def recv(self) -> str:
        while not self._closed:
            if self._inbox:
                return self._inbox.popleft()
            # No script left: park forever like a live socket awaiting frames.
            await asyncio.sleep(3600)
        raise ConnectionError("socket torn down")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self._closed = True


class MockHTTPResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload: dict[str, Any] = json_data if json_data is not None else {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception("HTTP %d" % self.status_code)


# ---------------------------------------------------------------------------
# Binance sample frames (only fields asserted somewhere are load-bearing)
# ---------------------------------------------------------------------------

_FRAME_TIME = 1711000043210


def _frame(event: str, **fields: Any) -> dict[str, Any]:
    return {"e": event, "E": _FRAME_TIME, **fields}


@pytest.fixture
def sample_binance_ticker() -> dict[str, Any]:
    return _frame(
        "24hrTicker",
        s="BTCUSDT",
        c="67000.00",
        b="66999.10",
        a="67000.90",
        v="9876.54",
        q="661741000.00",
        h="68123.00",
        l="65987.00",
        P="2.35",
    )


@pytest.fixture
def sample_binance_trade() -> dict[str, Any]:
    return _frame("trade", s="BTCUSDT", t=987654321, p="67001.00", q="0.75", m=False)


@pytest.fixture
def sample_binance_kline() -> dict[str, Any]:
    return _frame(
        "kline",
        s="BTCUSDT",
        k={
            "t": 1710993600000,
            "T": 1710997199999,
            "s": "BTCUSDT",
            "i": "1h",
            "o": "66811.00",
            "h": "67189.00",
            "l": "66722.00",
            "c": "67040.00",
            "v": "777.77",
            "x": True,
        },
    )


@pytest.fixture
def sample_binance_depth_update() -> dict[str, Any]:
    return _frame(
        "depthUpdate",
        s="BTCUSDT",
        U=100001,
        u=100009,
        b=[["66998.50", "1.25"], ["66997.00", "4.00"]],
        a=[["67000.50", "0.75"], ["67002.00", "2.25"]],
    )
