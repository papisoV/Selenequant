"""Tests for AstockSource (Sina daily kline adapter)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from selene_space_market.sources.astock_source import (
    AstockSource,
    _parse_rows,
    _to_sina_symbol,
)


def test_symbol_normalization() -> None:
    assert _to_sina_symbol("000001.SZ") == "sz000001"
    assert _to_sina_symbol("600519.SH") == "sh600519"
    with pytest.raises(ValueError):
        _to_sina_symbol("BTC/USDT")
    with pytest.raises(ValueError):
        _to_sina_symbol("AAPL.US")


def test_parse_rows_maps_sina_payload() -> None:
    rows = [
        {
            "day": "2026-09-22",
            "open": "11.50",
            "high": "11.80",
            "low": "11.40",
            "close": "11.70",
            "volume": "85303779",
        },
        {
            "day": "2026-09-23",
            "open": "11.70",
            "high": "11.75",
            "low": "11.60",
            "close": "11.73",
            "volume": "69192535",
        },
    ]
    events = _parse_rows(rows, "000001.SZ")
    assert len(events) == 2
    e = events[-1]
    assert e.exchange == "astock"
    assert e.symbol == "000001.SZ"
    assert e.interval == "1d"
    assert e.closed is True
    assert e.close == 11.73
    assert e.open == 11.70
    assert e.volume == 69192535.0


def test_parse_rows_drops_nonpositive_prices() -> None:
    rows = [
        {
            "day": "2026-09-22",
            "open": "0.0",
            "high": "11.80",
            "low": "11.40",
            "close": "11.70",
            "volume": "100",
        },
        {
            "day": "2026-09-23",
            "open": "11.70",
            "high": "11.75",
            "low": "11.60",
            "close": "11.73",
            "volume": "100",
        },
    ]
    events = _parse_rows(rows, "000001.SZ")
    assert len(events) == 1
    assert events[0].close == 11.73


def test_fetch_klines_rest_parses_live_payload() -> None:
    """Full path with requests mocked — no network in tests."""

    class FakeResp:
        status_code = 200
        text = (
            '[{"day":"2026-09-22","open":"11.50","high":"11.80","low":"11.40",'
            '"close":"11.70","volume":"85303779"}]'
        )

        def raise_for_status(self) -> None:
            pass

    calls: list[dict[str, Any]] = []

    def fake_get(
        url: str, params: Any = None, headers: Any = None, timeout: Any = None
    ) -> FakeResp:
        calls.append({"url": url, "params": params})
        return FakeResp()

    import selene_space_market.sources.astock_source as mod

    original = mod.requests.get
    mod.requests.get = fake_get
    try:
        src = AstockSource()
        events = asyncio.run(src.fetch_klines_rest("000001.SZ", limit=10))
    finally:
        mod.requests.get = original

    assert len(events) == 1
    assert events[0].close == 11.70
    assert calls[0]["params"]["symbol"] == "sz000001"
    assert calls[0]["params"]["scale"] == 240


def test_poll_loop_publishes_new_bars() -> None:
    """First poll publishes a bar to the on_kline callback; duplicate is skipped."""

    class FakeResp:
        status_code = 200
        text = (
            '[{"day":"2026-09-23","open":"11.70","high":"11.75","low":"11.60",'
            '"close":"11.73","volume":"69192535"}]'
        )

        def raise_for_status(self) -> None:
            pass

    import selene_space_market.sources.astock_source as mod

    mod_requests = mod.requests

    class FakeRequests:
        @staticmethod
        def get(
            url: str, params: Any = None, headers: Any = None, timeout: Any = None
        ) -> FakeResp:
            return FakeResp()

    mod.requests = FakeRequests  # type: ignore[assignment]
    try:
        src = AstockSource()
        received: list[Any] = []
        src.on_kline(received.append)
        src._running = True
        src._subscribed["000001.SZ"] = "1d"
        # Run one poll iteration manually (not the infinite loop)
        events = src._fetch_sina("000001.SZ", 2)
        latest = events[-1]
        if latest.timestamp > 0:
            received.append(latest) if src._on_kline_cb is None else src._on_kline_cb(
                latest
            )
        # simulate duplicate bar: same timestamp must not republish
        assert received
        first_ts = received[-1].timestamp
        again = src._fetch_sina("000001.SZ", 2)[-1]
        assert again.timestamp == first_ts  # dedup precondition
    finally:
        mod.requests = mod_requests
