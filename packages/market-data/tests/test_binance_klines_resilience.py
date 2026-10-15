"""BinanceSource.fetch_klines_rest resilience — mirror rotation (spot),
retry/backoff, and the latest-window page cache."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from selene_space_market.sources.binance_source import BinanceSource


def _rows(count: int = 3) -> list[list]:
    base = 1_711_000_000_000
    return [
        [
            base + i * 3_600_000,
            "100",
            "110",
            "95",
            "105",
            "10",
            base,
            5,
            5,
            "0",
            "0",
            "0",
        ]
        for i in range(count)
    ]


def _resp(status: int = 200, payload: list | None = None) -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else _rows()
    if status >= 400:
        resp.request = httpx.Request("GET", "https://x")
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"{status}", request=resp.request, response=resp
            )
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _source(market_type: str = "spot") -> BinanceSource:
    return BinanceSource(market_type=market_type)  # type: ignore[arg-type]


def _mock_get(fn) -> AsyncMock:
    """Wrap a sync (url, **kw) -> Response fn as an awaitable gateway.get."""
    mock = AsyncMock()
    mock.side_effect = lambda url, **kw: fn(url, **kw)
    return mock


def _ok(calls: list[str], url: str) -> httpx.Response:
    calls.append(url)
    return _resp(200, _rows())


class TestMirrorRotation:
    def test_spot_primary_ok_no_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        src = _source("spot")
        calls: list[str] = []
        monkeypatch.setattr(
            src._http, "get", _mock_get(lambda url, **kw: _ok(calls, url))
        )
        out = asyncio.run(src.fetch_klines_rest("BTC/USDT"))
        assert len(out) == 3
        assert len(calls) == 1

    def test_spot_primary_429_falls_to_mirror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _source("spot")
        calls: list[str] = []
        # primary exhausts 3 attempts (initial + 2 retries) on 429, mirror serves
        seq = [_resp(429), _resp(429), _resp(429)]

        def fake_get(url: str, **kw: object) -> httpx.Response:
            calls.append(url)
            if seq:
                return seq.pop(0)
            return _resp(200, _rows())

        monkeypatch.setattr(src._http, "get", _mock_get(fake_get))
        monkeypatch.setattr(
            "selene_space_market.sources.binance_source.asyncio.sleep", asyncio.sleep
        )
        out = asyncio.run(src.fetch_klines_rest("BTC/USDT"))
        assert len(out) == 3
        urls = {u.split("/")[2] for u in calls}
        assert urls == {"api.binance.com", "data-api.binance.vision"}

    def test_spot_deterministic_4xx_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _source("spot")
        calls: list[str] = []

        def bad_request(url: str, **kw: object) -> httpx.Response:
            calls.append(url)
            return _resp(400)

        monkeypatch.setattr(src._http, "get", _mock_get(bad_request))
        monkeypatch.setattr(
            "selene_space_market.sources.binance_source.asyncio.sleep", asyncio.sleep
        )
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(src.fetch_klines_rest("BAD??/USDT"))
        assert len(calls) == 1  # no retry, no mirror hop on deterministic 4xx

    def test_all_urls_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        src = _source("spot")
        calls: list[str] = []

        def server_error(url: str, **kw: object) -> httpx.Response:
            calls.append(url)
            return _resp(503)

        monkeypatch.setattr(src._http, "get", _mock_get(server_error))
        monkeypatch.setattr(
            "selene_space_market.sources.binance_source.asyncio.sleep", asyncio.sleep
        )
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(src.fetch_klines_rest("BTC/USDT"))
        assert len(calls) == 2 * 3  # 2 URLs × (1 initial + 2 retries)

    def test_futures_single_url_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        src = _source("futures")
        calls: list[str] = []
        state = {"n": 0}

        def fake_get(url: str, **kw: object) -> httpx.Response:
            calls.append(url)
            state["n"] += 1
            if state["n"] < 3:
                return _resp(503)
            return _resp(200, _rows())

        monkeypatch.setattr(src._http, "get", _mock_get(fake_get))
        monkeypatch.setattr(
            "selene_space_market.sources.binance_source.asyncio.sleep", asyncio.sleep
        )
        out = asyncio.run(src.fetch_klines_rest("BTC/USDT"))
        assert len(out) == 3
        assert len(calls) == 3


class TestLatestWindowPageCache:
    def test_repeat_poll_within_ttl_hits_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _source("spot")
        calls: list[str] = []
        monkeypatch.setattr(
            src._http, "get", _mock_get(lambda url, **kw: _ok(calls, url))
        )
        first = asyncio.run(src.fetch_klines_rest("BTC/USDT", "1h"))
        second = asyncio.run(src.fetch_klines_rest("BTC/USDT", "1h"))
        assert len(calls) == 1  # second call served from page cache
        assert [k.timestamp for k in second] == [k.timestamp for k in first]

    def test_end_time_bypasses_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        src = _source("spot")
        calls: list[str] = []
        monkeypatch.setattr(
            src._http, "get", _mock_get(lambda url, **kw: _ok(calls, url))
        )
        asyncio.run(src.fetch_klines_rest("BTC/USDT", "1h"))
        asyncio.run(src.fetch_klines_rest("BTC/USDT", "1h", end_time=1_711_000_000_000))
        asyncio.run(src.fetch_klines_rest("BTC/USDT", "1h", end_time=1_710_900_000_000))
        assert len(calls) == 3

    def test_different_interval_is_different_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _source("spot")
        calls: list[str] = []
        monkeypatch.setattr(
            src._http, "get", _mock_get(lambda url, **kw: _ok(calls, url))
        )
        asyncio.run(src.fetch_klines_rest("BTC/USDT", "1h"))
        asyncio.run(src.fetch_klines_rest("BTC/USDT", "4h"))
        assert len(calls) == 2
