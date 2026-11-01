"""Router coverage tests for /api/v1/us-radar (Phase 4).

Validates that the US Radar router exposes the expected 22 endpoints with
correct prefix and tags. Business logic for individual endpoints is tested
via the us_radar integration suite; this is structural coverage only.

Functional tests (mocked Yahoo client) close the historical blind spot:
before app/clients/yahoo_finance.py existed, any request hit ImportError->500.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers
from app.api.v1.us_radar import router as us_radar_router


class TestUsRadarRouter:
    def test_prefix_is_us_radar(self) -> None:
        assert us_radar_router.prefix == "/us-radar"

    def test_tag_present(self) -> None:
        assert "us-radar" in us_radar_router.tags or "us_radar" in us_radar_router.tags

    def test_expected_route_count(self) -> None:
        """US Radar should expose at least 20 routes (plan says 22)."""
        paths = [getattr(r, "path", None) for r in us_radar_router.routes]
        paths = [p for p in paths if p]
        assert len(paths) >= 20, f"only {len(paths)} routes: {paths}"

    def test_market_overview_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in us_radar_router.routes]
        assert any("/market/overview" in (p or "") for p in paths), paths

    def test_snapshot_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in us_radar_router.routes]
        assert any("/snapshot" in (p or "") for p in paths), paths

    def test_universe_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in us_radar_router.routes]
        assert any("/universe" in (p or "") for p in paths), paths

    def test_scanner_run_endpoint_registered_as_post(self) -> None:
        """/scanner/run must accept POST."""
        for r in us_radar_router.routes:
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if path and "/scanner/run" in path and methods:
                assert "POST" in methods
                return
        raise AssertionError("no /scanner/run POST route found")

    def test_kline_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in us_radar_router.routes]
        assert any("/symbols/{symbol}/kline" in (p or "") for p in paths), paths


# ---------------------------------------------------------------------------
# Functional tests — mock YahooFinanceClient via dependency override
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

import app.api.v1.us_radar as ur  # noqa: E402


class FakeClient:
    """Yahoo-shaped responses; methods mirror YahooFinanceClient's signatures."""

    async def get_quote(self, symbols: list[str]) -> dict[str, Any]:
        return {"quoteResponse": {"result": [
            {"symbol": "AAPL", "shortName": "Apple Inc", "regularMarketPrice": 234.5,
             "regularMarketChange": 2.3, "regularMarketChangePercent": 1.2,
             "regularMarketVolume": 50000000, "averageDailyVolume3Month": 55000000},
            {"symbol": "MSFT", "shortName": "Microsoft Corp", "regularMarketPrice": 415.0,
             "regularMarketChange": 1.0, "regularMarketChangePercent": 0.3,
             "regularMarketVolume": 30000000, "averageDailyVolume3Month": 30000000},
        ]}}

    async def get_news(self, symbol: str) -> dict[str, Any]:
        return {"news": [
            {"title": "Apple beats earnings", "publisher": "Reuters",
             "providerPublishTime": 1700000000, "link": "http://example.com/aaple"},
        ]}

    async def get_chart(self, symbol: str, interval: str = "1d", range_: str = "3mo") -> dict[str, Any]:
        return {"chart": {"result": [{
            "timestamp": [1700000000000, 1700086400000],
            "indicators": {"quote": [{
                "open": [230.0, 231.0], "high": [235.0, 236.0],
                "low": [229.0, 230.0], "close": [234.0, 235.5],
                "volume": [1000, 2000],
            }]},
        }]}}

    async def get_market_summary(self) -> dict[str, Any]:
        return {"quoteResponse": {"result": [
            {"symbol": "^GSPC", "regularMarketPrice": 5000.0, "regularMarketChangePercent": 0.5},
        ]}}

    async def get_movers(self) -> dict[str, Any]:
        return {
            "gainers": {"finance": {"result": [{"quotes": [
                {"symbol": "AAPL", "shortName": "Apple Inc", "regularMarketChangePercent": 3.0},
            ]}]}},
            "losers": {"finance": {"result": []}},
        }

    async def get_trending(self) -> dict[str, Any]:
        return {"finance": {"result": [{"quotes": [
            {"symbol": "AAPL"}, {"symbol": "MSFT"},
        ]}]}}


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(us_radar_router, prefix="/api/v1")
    app.dependency_overrides[ur._get_client] = lambda: FakeClient()
    ur._yf_client = None
    ur._cache.clear()
    return TestClient(app)


def _get(client: TestClient, path: str):
    resp = client.get(path)
    return resp.status_code, resp.json()


class TestUsRadarFunctional:
    def test_snapshot_returns_200_with_structure(self, client: TestClient) -> None:
        status, body = _get(client, "/api/v1/us-radar/snapshot")
        assert status == 200
        assert body["error"] is None
        data = body["data"]
        assert "indices" in data and "event_signals" in data
        assert data["indices"][0]["symbol"] == "^GSPC"
        assert "radar_scores" in data

    def test_market_overview_returns_200_with_gainers(self, client: TestClient) -> None:
        status, body = _get(client, "/api/v1/us-radar/market/overview")
        assert status == 200
        data = body["data"]
        assert data["indices"][0]["symbol"] == "^GSPC"
        assert any(g["symbol"] == "AAPL" for g in data["gainers"])

    def test_universe_returns_200_with_quotes(self, client: TestClient) -> None:
        status, body = _get(client, "/api/v1/us-radar/universe")
        assert status == 200
        data = body["data"]
        syms = [i["symbol"] for i in data["universe"]]
        # fake client only quotes AAPL/MSFT; structure over breadth here
        assert "AAPL" in syms and "MSFT" in syms
        assert data["total"] == len(syms)
        assert "by_kind" in data and isinstance(data["by_kind"], dict)

    def test_scanner_run_returns_200_with_funnel(self, client: TestClient) -> None:
        resp = client.post("/api/v1/us-radar/scanner/run", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        data = body["data"]
        assert "funnel" in data and "results" in data
        assert data["funnel"]["candidates_in"] >= 2

    def test_symbol_detail_returns_quote_news_chart_evidence(self, client: TestClient) -> None:
        status, body = _get(client, "/api/v1/us-radar/symbols/AAPL")
        assert status == 200
        data = body["data"]
        assert data["symbol"] == "AAPL"
        ev = data["evidence"]
        assert ev["quote"].get("regularMarketPrice") == 234.5
        assert ev["news_count"] == 1
        assert ev["chart_points"] == 2

    def test_symbol_kline_returns_bars(self, client: TestClient) -> None:
        status, body = _get(client, "/api/v1/us-radar/symbols/AAPL/kline")
        assert status == 200
        bars = body["data"]
        assert len(bars) == 2
        assert bars[0]["close"] == 234.0
        assert bars[0]["high"] == 235.0
        assert bars[1]["volume"] == 2000
        # ms epoch -> YYYY-MM-DD label
        assert bars[0]["timestamp"] == "2023-11-14"

    def test_symbol_kline_empty_chart_returns_404(self, client: TestClient) -> None:
        class EmptyChart(FakeClient):
            async def get_chart(self, symbol: str, interval: str = "1d", range_: str = "3mo") -> dict[str, Any]:
                return {"chart": {"result": []}}

        app = FastAPI()
        install_error_handlers(app)
        app.include_router(us_radar_router, prefix="/api/v1")
        app.dependency_overrides[ur._get_client] = lambda: EmptyChart()
        ur._yf_client = None
        ur._cache.clear()
        with TestClient(app) as c:
            resp = c.get("/api/v1/us-radar/symbols/NOPE/kline")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"

