"""Mission thesis API acceptance tests (plan S3).

route count == 20 断言在此文件(missions router 13 存量 + 7 attribution 端点,含 GET 归因行)。
test_api_research.py 存量零触碰。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1.research_missions import router as missions_router
from app.core import dependencies as deps
from app.main import app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


def test_missions_router_route_count() -> None:
    paths = [getattr(r, "path", None) for r in missions_router.routes]
    paths = [p for p in paths if p]
    assert len(paths) == 20, f"expected 20 routes, got {len(paths)}: {paths}"
    for required in (
        "/research/missions",
        "/research/missions/{mission_id}/thesis",
        "/research/strategy/{strategy_id}/state",
    ):
        assert required in paths, f"missing {required}"


@pytest_asyncio.fixture
async def client_and_db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        deps.set_db_manager(db)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, db
        deps.set_db_manager(None)


async def _create_mission(db: AsyncSQLiteManager) -> str:
    await db.execute(
        """
        INSERT INTO research_missions
            (id, title, question, subject_type, subject_id, phase, stage,
             created_by, opened_at, updated_at)
        VALUES ('mis-t01', 'Keltner 重验证',
                'Keltner 突破在 SOL 4h 上仍有正期望吗?',
                'strategy', 'st-keltner', 'investigation', 'opened',
                'selene', '2026-09-23T00:00:00+00:00', '2026-09-23T00:00:00+00:00')
        """
    )
    return "mis-t01"


async def _pillar_id(client: AsyncClient, mission_id: str) -> str:
    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/pillars",
        json={"claim": "突破保持正期望", "priority": "core"},
    )
    assert r.status_code == 200, r.text
    pillar_id = r.json()["pillar_id"]
    assert isinstance(pillar_id, str)
    return pillar_id


@pytest.mark.asyncio
async def test_pillar_create_and_thesis_scorecard_happy_path(client_and_db):
    client, db = client_and_db
    mission_id = await _create_mission(db)
    await _pillar_id(client, mission_id)

    r = await client.get(f"/api/v1/research/missions/{mission_id}/thesis")
    assert r.status_code == 200
    body = r.json()
    assert body["aggregate"] == "untested"
    assert body["aggregate_reason"] == "活跃支柱状态:untested×1"
    assert body["requires_override"] is False
    assert len(body["pillars"]) == 1
    assert body["pillars"][0]["claim"] == "突破保持正期望"
    assert body["signal_distribution"] == {}
    assert body["evidence_count"] == 0
    assert body["evidence"] == []


@pytest.mark.asyncio
async def test_evidence_append_updates_scorecard_distribution(client_and_db):
    client, db = client_and_db
    mission_id = await _create_mission(db)
    pillar_id = await _pillar_id(client, mission_id)

    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/evidence",
        json={
            "pillar_id": pillar_id,
            "occurred_at": "2026-09-22T10:00:00+00:00",
            "source_type": "backtest",
            "source_ref": "bt-b62d32ac",
            "fact": "241 trades sharpe 1.9",
            "interpretation": "edge holds",
            "signal": "confirming",
        },
    )
    assert r.status_code == 200, r.text

    body = (await client.get(f"/api/v1/research/missions/{mission_id}/thesis")).json()
    assert body["evidence_count"] == 1
    assert body["signal_distribution"] == {"confirming": 1}
    assert len(body["evidence"]) == 1
    assert body["evidence"][0]["fact"] == "241 trades sharpe 1.9"
    assert body["evidence"][0]["source_ref"] == "bt-b62d32ac"


@pytest.mark.asyncio
async def test_pillar_status_update_flow(client_and_db):
    client, db = client_and_db
    mission_id = await _create_mission(db)
    pillar_id = await _pillar_id(client, mission_id)

    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/pillars/{pillar_id}/status",
        json={"status": "impaired", "status_note": "回撤超预期"},
    )
    assert r.status_code == 200
    assert r.json()["pillar"]["status"] == "impaired"

    body = (await client.get(f"/api/v1/research/missions/{mission_id}/thesis")).json()
    assert body["pillars"][0]["status"] == "impaired"


@pytest.mark.asyncio
async def test_drift_check_roundtrip(client_and_db):
    client, db = client_and_db
    mission_id = await _create_mission(db)

    patterns = {
        k: {"present": False}
        for k in (
            "reason_changed",
            "catalyst_laundering",
            "valuation_substitution",
            "dismissal_by_horizon",
            "protection_as_reason",
        )
    }
    patterns["catalyst_laundering"] = {"present": True, "note": "叙事换锚"}
    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/drift-check",
        json={"patterns": patterns, "checked_by": "user", "note": "weekly"},
    )
    assert r.status_code == 200

    body = (await client.get(f"/api/v1/research/missions/{mission_id}/thesis")).json()
    assert body["last_drift_check"] is not None
    assert body["last_drift_check"]["kind"] == "drift_check"


@pytest.mark.asyncio
async def test_thesis_404_for_unknown_mission(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/v1/research/missions/mis-nope/thesis")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_invalid_vocab_rejected_422(client_and_db):
    client, db = client_and_db
    mission_id = await _create_mission(db)

    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/pillars",
        json={"claim": "c", "priority": "critical"},
    )
    assert r.status_code == 422

    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/evidence",
        json={
            "pillar_id": "pil-xyz",
            "occurred_at": "2026-09-22",
            "source_type": "backtest",
            "fact": "f",
            "interpretation": "i",
            "signal": "very_good",
        },
    )
    assert r.status_code == 422

    r = await client.post(
        f"/api/v1/research/missions/{mission_id}/drift-check",
        json={"patterns": {"reason_changed": {"present": False}}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_error_responses_do_not_leak_internal_detail(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/v1/research/missions/mis-nope/thesis")
    assert r.status_code == 404
    assert "mission_thesis" not in r.text.lower()
    assert "traceback" not in r.text.lower()
