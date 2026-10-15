"""Review lifecycle PATCH endpoint for saved alpha strategies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.v1.alpha as alpha_mod
from app.main import create_app


@pytest.fixture()
def _fake_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vdir = tmp_path / "alpha_v7_mining"
    vdir.mkdir()
    target = vdir / "best_meme_csi300.json"
    target.write_text(
        json.dumps({"version": "v7_mining_csi300", "best_formula": {"tokens": [1]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(alpha_mod, "_strategies_dir", tmp_path)
    return target


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_patch_review_status_persists(_fake_strategy: Path) -> None:
    async with _client() as c:
        resp = await c.patch(
            "/api/v1/alpha/saved-strategies/best_meme_csi300/review-status",
            json={"review_status": "paper_trading"},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["review_status"] == "paper_trading"
    on_disk = json.loads(_fake_strategy.read_text(encoding="utf-8"))
    assert on_disk["review_status"] == "paper_trading"


@pytest.mark.asyncio
async def test_patch_rejects_invalid_status(_fake_strategy: Path) -> None:
    async with _client() as c:
        resp = await c.patch(
            "/api/v1/alpha/saved-strategies/best_meme_csi300/review-status",
            json={"review_status": "bogus"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_404_unknown_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(alpha_mod, "_strategies_dir", tmp_path)
    async with _client() as c:
        resp = await c.patch(
            "/api/v1/alpha/saved-strategies/nope/review-status",
            json={"review_status": "inspected"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_includes_review_status(_fake_strategy: Path) -> None:
    _fake_strategy.write_text(
        json.dumps(
            {
                "version": "v7_mining_csi300",
                "best_formula": {"tokens": [1]},
                "review_status": "inspected",
            }
        ),
        encoding="utf-8",
    )
    async with _client() as c:
        resp = await c.get("/api/v1/alpha/saved-strategies")
    assert resp.status_code == 200
    items = resp.json()["data"]["strategies"]
    assert items and items[0]["review_status"] == "inspected"


@pytest.mark.asyncio
async def test_batch_review_status_persists(_fake_strategy: Path) -> None:
    async with _client() as c:
        resp = await c.patch(
            "/api/v1/alpha/saved-strategies/review-status/batch",
            json={
                "strategy_names": ["best_meme_csi300", "ghost"],
                "review_status": "inspected",
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["updated"] == [
        {"name": "best_meme_csi300", "review_status": "inspected"}
    ]
    assert data["missing"] == ["ghost"]
    on_disk = json.loads(_fake_strategy.read_text(encoding="utf-8"))
    assert on_disk["review_status"] == "inspected"


@pytest.mark.asyncio
async def test_batch_rejects_invalid_status(_fake_strategy: Path) -> None:
    async with _client() as c:
        resp = await c.patch(
            "/api/v1/alpha/saved-strategies/review-status/batch",
            json={"strategy_names": ["best_meme_csi300"], "review_status": "bogus"},
        )
    assert resp.status_code == 422
