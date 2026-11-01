"""Tests for /api/v1/options/vol-surface (Phase 4)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.options import router as options_router
from app.core.dependencies import get_market_service
from selene_space_types.derivatives import OptionsInstrument, OptionsSummary, OptionsTicker


class FakeMarketService:
    """Minimal stand-in exposing the stores the vol-surface endpoint reads."""

    def __init__(self) -> None:
        self._options_instrument_store: dict[str, OptionsInstrument] = {}
        self._options_ticker_store: dict[str, OptionsTicker] = {}
        self._options_summary_store: dict[str, Any] = {}


def _make_app(svc: Any | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(options_router, prefix="/api/v1")
    if svc is not None:
        app.dependency_overrides[get_market_service] = lambda: svc
    return app


class TestVolSurfaceEndpoint:
    def test_returns_expected_shape(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "BTC"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        data = body["data"]
        assert data["underlying"] == "BTC"
        assert "ts" in data
        assert isinstance(data["ts"], int)
        assert isinstance(data["points"], list)
        assert isinstance(data["expiries"], list)
        assert isinstance(data["strikes"], list)

    def test_returns_empty_surface_without_store(self) -> None:
        """When no instrument store is attached, surface is empty but well-formed."""
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "BTC"})
        data = resp.json()["data"]
        assert data["points"] == []
        assert data["expiries"] == []
        assert data["strikes"] == []
        # Underlying + ts always present.
        assert data["underlying"] == "BTC"
        assert isinstance(data["ts"], int)

    def test_supports_eth(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "ETH"})
        assert resp.status_code == 200
        assert resp.json()["data"]["underlying"] == "ETH"

    def test_strikes_and_expiries_sorted(self) -> None:
        """Expiries (strings) and strikes (floats) should be sorted ascending."""
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "BTC"})
        data = resp.json()["data"]
        assert data["expiries"] == sorted(data["expiries"])
        assert data["strikes"] == sorted(data["strikes"])

    def test_point_shape_when_present(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "BTC"})
        for point in resp.json()["data"]["points"]:
            assert "expiry" in point
            assert "strike" in point
            assert "iv" in point
            assert isinstance(point["strike"], (int, float))
            assert isinstance(point["iv"], (int, float))

    def test_populated_store_yields_points_with_iv(self) -> None:
        """A populated instrument+ticker store must surface non-empty points with iv>0.

        Regression for the field mismatch that read `expiry`/`mark_iv`/`volume`
        while the models only carry `expiration_timestamp`/`implied_volatility`/
        `volume_24h`.
        """
        svc = FakeMarketService()
        svc._options_instrument_store["BTC-28MAR25-80000-C"] = OptionsInstrument(
            instrument_name="BTC-28MAR25-80000-C",
            base_currency="BTC",
            expiration_timestamp=1_742_630_400_000,  # 2025-03-22 08:00:00 UTC
            strike=80000.0,
            option_type="call",
        )
        svc._options_ticker_store["BTC-28MAR25-80000-C"] = OptionsTicker(
            instrument_name="BTC-28MAR25-80000-C",
            underlying_price=100000.0,
            implied_volatility=0.65,
            volume_24h=1200.0,
            timestamp=1_750_000_000_000,
        )
        app = _make_app(svc)
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "BTC"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["points"], "expected non-empty points from populated store"
        point = data["points"][0]
        assert point["iv"] > 0
        assert point["expiry"] == "2025-03-22"
        assert point["strike"] == 80000.0
        assert point["volume"] == 1200
        assert data["expiries"] == ["2025-03-22"]
        assert data["strikes"] == [80000.0]

    def test_missing_ticker_iv_does_not_crash(self) -> None:
        """Instrument without a matching ticker must yield iv=0, not crash."""
        svc = FakeMarketService()
        svc._options_instrument_store["BTC-28MAR25-90000-P"] = OptionsInstrument(
            instrument_name="BTC-28MAR25-90000-P",
            base_currency="BTC",
            expiration_timestamp=1_742_630_400_000,
            strike=90000.0,
            option_type="put",
        )
        app = _make_app(svc)
        client = TestClient(app)
        resp = client.get("/api/v1/options/vol-surface", params={"base_currency": "BTC"})
        assert resp.status_code == 200
        point = resp.json()["data"]["points"][0]
        assert point["iv"] == 0.0
        assert point["volume"] is None


class TestSummaryEndpoint:
    def test_populated_store_returns_nonempty_summaries(self) -> None:
        """Populated summary store must surface non-empty summaries (ADR-1 poller
        writes via fill_options_data; the endpoint reads _options_summary_store)."""
        svc = FakeMarketService()
        svc._options_summary_store["BTC:1742630400000"] = OptionsSummary(
            base_currency="BTC",
            expiration_timestamp=1_742_630_400_000,
            days_to_expiry=5.0,
            total_call_oi=5000.0,
            total_put_oi=3000.0,
            put_call_ratio=0.6,
            call_iv_atm=0.65,
            put_iv_atm=0.72,
            timestamp=1_750_000_000_000,
        )
        app = _make_app(svc)
        client = TestClient(app)
        resp = client.get("/api/v1/options/summary", params={"base_currency": "BTC"})
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["count"] == 1
        assert len(body["summaries"]) == 1
        summary = body["summaries"][0]
        assert summary["base_currency"] == "BTC"
        assert summary["expiration_timestamp"] == 1_742_630_400_000
        assert summary["total_call_oi"] == 5000.0
        assert summary["put_call_ratio"] == 0.6

    def test_eth_summaries_filtered_by_currency(self) -> None:
        svc = FakeMarketService()
        svc._options_summary_store["BTC:1742630400000"] = OptionsSummary(
            base_currency="BTC",
            expiration_timestamp=1_742_630_400_000,
            days_to_expiry=5.0,
            timestamp=1_750_000_000_000,
        )
        svc._options_summary_store["ETH:1744000000000"] = OptionsSummary(
            base_currency="ETH",
            expiration_timestamp=1_744_000_000_000,
            days_to_expiry=3.0,
            timestamp=1_750_000_000_000,
        )
        app = _make_app(svc)
        client = TestClient(app)
        resp = client.get("/api/v1/options/summary", params={"base_currency": "ETH"})
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["count"] == 1
        assert body["summaries"][0]["base_currency"] == "ETH"

    def test_empty_store_returns_well_formed_empty(self) -> None:
        app = _make_app(FakeMarketService())
        client = TestClient(app)
        resp = client.get("/api/v1/options/summary", params={"base_currency": "BTC"})
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["count"] == 0
        assert body["summaries"] == []
