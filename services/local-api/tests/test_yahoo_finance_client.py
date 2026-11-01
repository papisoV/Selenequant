"""Tests for YahooFinanceClient cookie+crumb auth (方向4 live-data fix).

Yahoo serves 401 "Invalid Crumb" / 429 when no cookie+crumb is attached.
The client fetches the cookie from fc.yahoo.com, the crumb from
/v1/test/getcrumb, sends crumb as a query param, and refreshes + retries
once on 401/403/429. All tests are offline (fake httpx.AsyncClient).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.clients import yahoo_finance as yf


class _Resp:
    def __init__(self, status_code: int, json: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://query1.finance.yahoo.com/")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )

    def json(self) -> dict | None:
        return self._json


_CHART_JSON = {
    "chart": {
        "result": [
            {
                "timestamp": [1700000000],
                "indicators": {
                    "quote": [
                        {
                            "open": [1.0],
                            "high": [2.0],
                            "low": [0.5],
                            "close": [1.5],
                            "volume": [100],
                        }
                    ]
                },
            }
        ]
    }
}


class _FakeAsyncClient:
    """Succeeds only when a crumb is attached to the chart request."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []
        self.crumb_count = 0
        self.is_closed = False

    async def get(
        self, url: str, params: dict | None = None, headers: dict | None = None
    ) -> _Resp:
        if "fc.yahoo.com" in url:
            self.cookies["A1"] = "cookie-v"
            return _Resp(200, text="ok")
        path = url.replace(yf.YAHOO_BASE, "")
        if path == yf._CRUMB_PATH:
            self.crumb_count += 1
            return _Resp(200, text=f"crumb-{self.crumb_count}")
        self.calls.append((path, dict(params or {})))
        if path.startswith("/v8/finance/chart/"):
            if params and params.get("crumb"):
                return _Resp(200, json=_CHART_JSON)
            return _Resp(401, json={"error": {"description": "Invalid Crumb"}})
        return _Resp(200, json={})

    async def aclose(self) -> None:
        self.is_closed = True


def _run(coro):
    return asyncio.run(coro)


def test_get_chart_fetches_cookie_then_crumb_and_sends_it():
    fake = _FakeAsyncClient()

    client = yf.YahooFinanceClient(client=fake)  # type: ignore[arg-type]
    data = _run(client.get_chart("AAPL", interval="1d", range_="1y"))

    assert data["chart"]["result"][0]["timestamp"] == [1700000000]
    assert fake.cookies.get("A1") == "cookie-v"  # cookie fetched from fc.yahoo.com
    assert fake.crumb_count == 1  # crumb fetched once
    path, params = fake.calls[0]
    assert path == "/v8/finance/chart/AAPL"
    assert params["crumb"] == "crumb-1"  # crumb attached to the request
    assert params["interval"] == "1d"


def test_get_chart_refreshes_crumb_and_retries_on_invalid_crumb():
    fake = _FakeAsyncClient()
    real_get = fake.get

    chart_calls = 0

    async def _get(url, params=None, headers=None):
        nonlocal chart_calls
        if url.replace(yf.YAHOO_BASE, "").startswith("/v8/finance/chart/"):
            chart_calls += 1
            if chart_calls == 1:
                return _Resp(401, json={"error": {"description": "Invalid Crumb"}})
        return await real_get(url, params, headers)

    fake.get = _get

    client = yf.YahooFinanceClient(client=fake)  # type: ignore[arg-type]
    data = _run(client.get_chart("AAPL"))

    assert data["chart"]["result"][0]["indicators"]["quote"][0]["close"] == [1.5]
    assert chart_calls == 2  # first 401, second after refresh succeeded
    assert fake.crumb_count == 2  # auth refreshed once
    assert fake.calls[0][1]["crumb"] == "crumb-2"  # retry carried the fresh crumb


def test_persistent_429_raises_yahoo_error():
    class _Always429(_FakeAsyncClient):
        async def get(self, url, params=None, headers=None):
            if "fc.yahoo.com" in url:
                return _Resp(200, text="ok")
            if url.replace(yf.YAHOO_BASE, "") == yf._CRUMB_PATH:
                return _Resp(200, text="crumb")
            return _Resp(429, json={})

    client = yf.YahooFinanceClient(client=_Always429())  # type: ignore[arg-type]
    with pytest.raises(yf.YahooFinanceError):
        _run(client.get_quote(["AAPL"]))


def test_get_movers_degrades_to_empty_on_screener_error():
    class _Always429(_FakeAsyncClient):
        async def get(self, url, params=None, headers=None):
            if "fc.yahoo.com" in url:
                return _Resp(200, text="ok")
            if url.replace(yf.YAHOO_BASE, "") == yf._CRUMB_PATH:
                return _Resp(200, text="crumb")
            return _Resp(429, json={})

    client = yf.YahooFinanceClient(client=_Always429())  # type: ignore[arg-type]
    movers = _run(client.get_movers())
    assert movers["gainers"] == {"finance": {"result": []}}
    assert movers["losers"] == {"finance": {"result": []}}


_TRENDING_JSON = {"finance": {"result": [{"quotes": [{"symbol": "NVDA"}]}]}}


def test_get_trending_sends_no_params_and_does_not_type_error():
    """Regression: _get() used to require params positionally, so the
    parameterless /v1/finance/trending/US path raised TypeError before any
    request was sent — /us-radar/snapshot degrading to 503 (live 2026-10-03)."""

    class _TrendingFake(_FakeAsyncClient):
        async def get(self, url, params=None, headers=None):
            if "fc.yahoo.com" in url:
                return _Resp(200, text="ok")
            path = url.replace(yf.YAHOO_BASE, "")
            if path == yf._CRUMB_PATH:
                return _Resp(200, text="crumb")
            self.calls.append((path, dict(params or {})))
            if path == "/v1/finance/trending/US":
                return _Resp(200, json=_TRENDING_JSON)
            return _Resp(200, json={})

    client = yf.YahooFinanceClient(client=_TrendingFake())  # type: ignore[arg-type]
    data = _run(client.get_trending())

    assert data["finance"]["result"][0]["quotes"][0]["symbol"] == "NVDA"
    trending_calls = [
        c for c in client._client.calls if c[0] == "/v1/finance/trending/US"
    ]
    assert len(trending_calls) == 1
    assert trending_calls[0][1] == {"crumb": "crumb"}  # crumb attached, no stray params
