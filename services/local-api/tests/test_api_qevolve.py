"""Tests for /api/v1/qevolve/current (research page endpoint)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import qevolve as qevolve_module
from app.api.v1.qevolve import router as qevolve_router


@pytest.fixture(autouse=True)
def _clear_evolve_runs() -> None:
    """Isolate the module-level in-memory store between tests."""
    qevolve_module._evolve_runs.clear()
    yield
    qevolve_module._evolve_runs.clear()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(qevolve_router, prefix="/api/v1")
    return app


def _make_client() -> TestClient:
    return TestClient(_make_app())


class TestQevolveCurrentEndpoint:
    def test_empty_store_returns_empty_data(self) -> None:
        # Guards route order: if /current were swallowed by /{evolve_id},
        # "current" would be treated as an evolve_id and return 404.
        client = _make_client()
        resp = client.get("/api/v1/qevolve/current")
        assert resp.status_code == 200
        assert resp.json()["data"] == {}

    def test_with_run_returns_expected_shape(self) -> None:
        client = _make_client()
        create_resp = client.post(
            "/api/v1/qevolve",
            json={
                "seed_strategy_id": "bb_v3",
                "generations": 3,
                "variants_per_gen": 4,
            },
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["data"]["id"]

        resp = client.get("/api/v1/qevolve/current")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["active_evolution"]
        assert isinstance(data["generation"], int)
        assert isinstance(data["best_fitness"], (int, float))
        assert isinstance(data["population_size"], int)
