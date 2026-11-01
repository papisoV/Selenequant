"""Tests for alpha tearsheet endpoints (single + compare)."""

from __future__ import annotations

import json
from pathlib import Path

import app.api.v1.alpha as alpha_mod
import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
def strategy_files(tmp_path: Path, monkeypatch) -> Path:
    v5 = tmp_path / "alpha_v5"
    v5.mkdir()
    payload = {
        "name": "strat_a",
        "version": "v4",
        "formula": {"tokens": [1, 2], "readable": "RET ADD MEAN2"},
        "strategies": {},
    }
    (v5 / "strat_a.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(alpha_mod, "_strategies_dir", tmp_path)
    return v5


@pytest.mark.asyncio
async def test_tearsheet_404_for_unknown_strategy(strategy_files: Path) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/alpha/saved-strategies/ghost/tearsheet",
            params={"symbol": "000001.SZ", "interval": "1d", "exchange": "astock"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tearsheet_unavailable_on_empty_klines(
    strategy_files: Path, monkeypatch
) -> None:
    def fake_compute(
        tokens, symbol, interval, exchange, market_type="spot", vocab_version="v4"
    ):
        return {
            "available": False,
            "reason": f"Insufficient kline data for {symbol} (0 bars)",
        }

    monkeypatch.setattr(alpha_mod, "_compute_tearsheet", fake_compute)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/alpha/saved-strategies/strat_a/tearsheet",
            params={"symbol": "000001.SZ", "interval": "1d", "exchange": "astock"},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert "Insufficient" in data["reason"]


@pytest.mark.asyncio
async def test_compare_reports_missing(strategy_files: Path, monkeypatch) -> None:
    def fake_compute(
        tokens, symbol, interval, exchange, market_type="spot", vocab_version="v4"
    ):
        return {
            "available": True,
            "symbol": symbol,
            "interval": interval,
            "summary": {
                "observations": 10,
                "ic_mean": 0.01,
                "turnover": 0.1,
                "abs_mean": 1.0,
            },
        }

    monkeypatch.setattr(alpha_mod, "_compute_tearsheet", fake_compute)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/alpha/saved-strategies/tearsheet/compare",
            json={
                "strategy_names": ["strat_a", "ghost"],
                "symbol": "000001.SZ",
                "interval": "1d",
                "exchange": "astock",
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["missing"] == ["ghost"]
    assert len(data["tearsheets"]) == 1
    assert data["tearsheets"][0]["strategy_name"] == "strat_a"


def test_saved_strategy_tokens_extracts_version() -> None:
    tokens, version = alpha_mod._saved_strategy_tokens(
        {"version": "v7_mining", "best_formula": {"tokens": [3, 4, 5]}}
    )
    assert tokens == [3, 4, 5]
    assert version == "v7_mining"
