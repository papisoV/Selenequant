"""Favorites store bare A-share codes; every consumer boundary adds the suffix.

Storage form is decided by the frontend contract: ``toPair`` strips every
exchange suffix, so the watchlist holds ``"600519"`` — and ``/chart/bars``
hands that bare code straight to the A-share daily fetch, which is why the
storage side must not be "normalized" to ``"600519.SH"``. The feeds, the
KlineDB partitions, ``/ashare/deviation`` and the anomaly detector all key on
the suffixed form, so the conversion happens at those boundaries instead.

These tests cover the two API write paths (PUT and toggle) and pin that both
fold the two forms of the same listing into one stored row.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.v1.favorites as favorites_mod


class _Settings:
    """Minimal SettingsService: same dedupe rule as the real one."""

    def __init__(self, symbols: list[str] | None = None) -> None:
        self._symbols = list(symbols or [])

    def get_favorite_symbols(self) -> list[str]:
        return list(self._symbols)

    def set_favorite_symbols(self, symbols: list[str]) -> list[str]:
        from app.core.settings_service import _dedupe_symbols

        self._symbols = _dedupe_symbols(symbols)
        return list(self._symbols)


@pytest.fixture
def settings() -> _Settings:
    return _Settings()


@pytest.fixture
def client(settings: _Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # The route calls the module-level getter directly (no Depends), so patch
    # the name the module actually resolves.
    monkeypatch.setattr(favorites_mod, "get_settings_service", lambda: settings)
    # No sync side effects: these tests are about stored form, not routing.
    monkeypatch.setattr(favorites_mod, "_maybe_sync_new_symbols", lambda s: None)
    app = FastAPI()
    app.include_router(favorites_mod.router, prefix="/api/v1")
    return TestClient(app)


def test_put_stores_bare_ashare(client: TestClient) -> None:
    resp = client.put("/api/v1/favorites", json={"symbols": ["600519.SH"]})
    assert resp.status_code == 200
    assert resp.json()["data"]["symbols"] == ["600519"]


def test_put_dedupes_the_two_forms_order_preserving(client: TestClient) -> None:
    resp = client.put(
        "/api/v1/favorites", json={"symbols": ["300750.SZ", "AAPL", "300750"]}
    )
    assert resp.json()["data"]["symbols"] == ["300750", "AAPL"]


def test_put_leaves_other_markets_untouched(client: TestClient) -> None:
    symbols = ["BTC/USDT", "AAPL", "BRK.A", "CU0", "600519"]
    resp = client.put("/api/v1/favorites", json={"symbols": symbols})
    assert resp.json()["data"]["symbols"] == symbols


def test_toggle_of_the_suffixed_form_removes_the_bare_row(
    client: TestClient, settings: _Settings
) -> None:
    # A client sending "600519.SH" must toggle the existing "600519", not add
    # a twin that the watchlist would render twice.
    settings.set_favorite_symbols(["600519"])
    resp = client.post("/api/v1/favorites/toggle", json={"symbol": "600519.SH"})
    assert resp.json()["data"]["symbols"] == []


def test_toggle_of_the_suffixed_form_adds_the_bare_row(
    client: TestClient, settings: _Settings
) -> None:
    resp = client.post("/api/v1/favorites/toggle", json={"symbol": "920002.BJ"})
    assert resp.json()["data"]["symbols"] == ["920002"]
