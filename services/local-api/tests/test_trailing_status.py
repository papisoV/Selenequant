"""GET /trading/trailing-status endpoint.

Empty list when the tiered trailing stop tracker is not wired; one status
dict per tracked symbol when it is (mocked — real tracker is owned by
StrategyRunner lifespan).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    from app.main import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _reset_tracker():
    from app.core.dependencies import set_tiered_trailing_tracker

    set_tiered_trailing_tracker(None)
    yield
    set_tiered_trailing_tracker(None)


def test_trailing_status_unwired_returns_empty(app):
    """Tracker not wired → 200 + empty data list, never 5xx."""
    client = TestClient(app)
    resp = client.get("/api/v1/trading/trailing-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"] == []


def test_trailing_status_returns_tracked_symbols(app):
    """Mock tracker wired → one status dict per symbol from status()."""
    from app.core.dependencies import set_tiered_trailing_tracker

    class _FakeTracker:
        def symbols(self) -> list[str]:
            return ["BTC/USDT", "ETH/USDT"]

        def status(self, symbol: str) -> dict | None:
            if symbol == "BTC/USDT":
                return {
                    "symbol": "BTC/USDT",
                    "entry_price": 100.0,
                    "peak_price": 120.0,
                    "current_tier": "base",
                    "profit_pct": 0.2,
                    "closed": False,
                    "stop_level": 105.0,
                }
            return None

    set_tiered_trailing_tracker(_FakeTracker())

    client = TestClient(app)
    resp = client.get("/api/v1/trading/trailing-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    # ETH/USDT status() → None must be skipped, not crash.
    assert body["data"] == [
        {
            "symbol": "BTC/USDT",
            "entry_price": 100.0,
            "peak_price": 120.0,
            "current_tier": "base",
            "profit_pct": 0.2,
            "closed": False,
            "stop_level": 105.0,
        }
    ]
