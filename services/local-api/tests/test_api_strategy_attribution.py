"""Strategy attribution API acceptance tests (plan §2.7).

5 端点 happy path + suggest 无写断言 + POST 撞 UNIQUE 409 + PUT 改判 +
rule 批插幂等(stats execution 档 == bundle 亏损数对账)。
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from app.api.v1.research_missions import router as missions_router
from app.core import dependencies as deps
from app.main import app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

_ATTR_BASE = "/api/v1/research"
_FAKE_TRADES = [
    {
        "symbol": "SOL/USDT",
        "side": "long",
        "entry_time": 1735689600000,
        "exit_time": 1735776000000,
        "entry_price": 100.0,
        "exit_price": 95.0,
        "quantity": 1.0,
        "pnl": -50.0,
    },
    {
        "symbol": "SOL/USDT",
        "side": "long",
        "entry_time": 1735862400000,
        "exit_time": 1735948800000,
        "entry_price": 100.0,
        "exit_price": 90.0,
        "quantity": 1.0,
        "pnl": -100.0,
    },
    {
        "symbol": "SOL/USDT",
        "side": "long",
        "entry_time": 1736035200000,
        "exit_time": 1736121600000,
        "entry_price": 100.0,
        "exit_price": 105.0,
        "quantity": 1.0,
        "pnl": 50.0,
    },
]


class FakeProvider:
    """generate_structured-only fake(镜像 openai_compat 的调用面)。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def generate_structured(self, messages, output_schema, **kwargs):
        self.calls += 1
        return dict(self.payload)


class FakeRuntime:
    def __init__(self, provider) -> None:
        self._llm = provider


async def _seed_strategy_and_backtest(db: AsyncSQLiteManager, drifted: bool):
    await db.execute(
        """
        INSERT INTO strategies (id, name, type, config_json)
        VALUES ('st-att', 'Keltner V2', 'trend', '{"symbols": ["SOL/USDT"],
                "interval": "4h", "parameters": {"ma_length": 20}}')
        """
    )
    bt_symbol = "BTC/USDT" if drifted else "SOL/USDT"
    await db.execute(
        """
        INSERT INTO backtests (id, strategy_id, start_date, end_date,
                               metrics_json, trades_count, trades_json,
                               symbol, interval)
        VALUES ('bt-att', 'st-att', '2026-01-01', '2026-01-10', '{}',
                3, ?, ?, '4h')
        """,
        (json.dumps(_FAKE_TRADES), bt_symbol),
    )


@pytest_asyncio.fixture
async def client_and_db(tmp_path, monkeypatch):
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    db_file = tmp_path / "ss.db"
    async with AsyncSQLiteManager(str(db_file), migration_runner=runner) as db:
        deps.set_db_manager(db)
        # suggest/POST/PUT/rules 经 _strategy_db_path() 另开只读连接读
        # strategies/backtests——必须指向同一个库,否则与 manager 库分叉 404
        from app.api.v1 import research_missions as rm

        monkeypatch.setattr(rm, "_strategy_db_path", lambda: db_file)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, db
        deps.set_db_manager(None)


# 1. categories + stats happy path -------------------------------------------------


@pytest.mark.asyncio
async def test_categories_and_stats_happy_path(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)

    r = await client.get(f"{_ATTR_BASE}/attribution/categories")
    assert r.status_code == 200, r.text
    cats = r.json()["categories"]
    assert sorted(c["code"] for c in cats) == [
        "assumption",
        "data",
        "execution",
        "regime_shift",
        "strategy",
    ]

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/1/attribution",
        json={"category_code": "strategy", "suggested_by": "director"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["attribution"]["episode_graph_id"] is None

    r = await client.get(f"{_ATTR_BASE}/attribution/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"] == {"strategy": 1}
    assert len(body["episodes"]) == 1
    assert body["episodes"][0]["strategy_id"] == "st-att"
    assert body["episodes"][0]["episode_n"] == 1


# 2. suggest: no-write + LLM unavailable degradation --------------------------------


@pytest.mark.asyncio
async def test_suggest_no_write_and_unavailable_null(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)
    deps.set_agent_runtime(None)

    rows_before = await db.execute(
        "SELECT COUNT(*) AS n FROM strategy_episode_attributions"
    )
    r = await client.get(f"{_ATTR_BASE}/strategy/st-att/state/1/attribution/suggest")
    assert r.status_code == 200, r.text
    assert r.json()["suggestion"] is None
    rows_after = await db.execute(
        "SELECT COUNT(*) AS n FROM strategy_episode_attributions"
    )
    assert rows_after[0]["n"] == rows_before[0]["n"] == 0


@pytest.mark.asyncio
async def test_suggest_with_fake_llm_returns_payload(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)
    provider = FakeProvider(
        {
            "category_code": "strategy",
            "rationale": "逻辑失效",
            "confidence": 0.8,
        }
    )
    deps.set_agent_runtime(FakeRuntime(provider))

    r = await client.get(f"{_ATTR_BASE}/strategy/st-att/state/1/attribution/suggest")
    assert r.status_code == 200, r.text
    sug = r.json()["suggestion"]
    assert sug["category_code"] == "strategy"
    assert sug["rationale"] == "逻辑失效"
    assert sug["confidence"] == 0.8
    rows = await db.execute("SELECT COUNT(*) AS n FROM strategy_episode_attributions")
    assert rows[0]["n"] == 0
    deps.set_agent_runtime(None)


@pytest.mark.asyncio
async def test_suggest_llm_out_of_vocab_returns_null(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)
    deps.set_agent_runtime(
        FakeRuntime(FakeProvider({"category_code": "luck", "rationale": "?"}))
    )
    r = await client.get(f"{_ATTR_BASE}/strategy/st-att/state/1/attribution/suggest")
    assert r.status_code == 200
    assert r.json()["suggestion"] is None
    assert r.json()["reason"] == "llm_out_of_vocab"
    deps.set_agent_runtime(None)


@pytest.mark.asyncio
async def test_suggest_unknown_episode_404(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)
    r = await client.get(f"{_ATTR_BASE}/strategy/st-att/state/99/attribution/suggest")
    assert r.status_code == 404


# 3. POST conflict 409 + PUT overwrite ------------------------------------------------


@pytest.mark.asyncio
async def test_post_conflict_409_then_put_overwrite(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/2/attribution",
        json={"category_code": "strategy", "suggested_by": "director"},
    )
    assert r.status_code == 200
    created_at = r.json()["attribution"]["created_at"]

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/2/attribution",
        json={"category_code": "data", "suggested_by": "llm"},
    )
    assert r.status_code == 409
    assert "already attributed" in r.json()["detail"]

    r = await client.put(
        f"{_ATTR_BASE}/strategy/st-att/state/2/attribution",
        json={"category_code": "regime_shift", "suggested_by": "director"},
    )
    assert r.status_code == 200, r.text
    row = r.json()["attribution"]
    assert row["category_code"] == "regime_shift"
    assert row["created_at"] == created_at

    r = await client.put(
        f"{_ATTR_BASE}/strategy/st-att/state/42/attribution",
        json={"category_code": "data", "suggested_by": "director"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_bad_category_400_bad_suggested_by_422(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/1/attribution",
        json={"category_code": "not_a_category", "suggested_by": "director"},
    )
    assert r.status_code == 400

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/1/attribution",
        json={"category_code": "data", "suggested_by": "oracle"},
    )
    assert r.status_code == 422


# 4. rules batch insert: idempotent, execution count == bundle losses ------------------


@pytest.mark.asyncio
async def test_rules_batch_idempotent_execution_matches_losses(client_and_db):
    client, db = client_and_db
    # drifted=true:声明 SOL vs 实跑 BTC → drift bundle,2 笔全亏(1、2)
    await _seed_strategy_and_backtest(db, drifted=True)

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/attribution/rules",
        json={},
    )
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == [1, 2]
    assert r.json()["rule_episode_ns"] == [1, 2]

    r2 = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/attribution/rules",
        json={},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["inserted"] == []

    r = await client.get(f"{_ATTR_BASE}/attribution/stats")
    body = r.json()
    assert body["counts"]["execution"] == 2  # == bundle 亏损数 N=2
    assert len(body["episodes"]) == 2
    assert all(e["suggested_by"] == "rule" for e in body["episodes"])


@pytest.mark.asyncio
async def test_rules_rejects_non_drift_bundle_422(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)
    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/attribution/rules",
        json={},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_attribution_row_and_404(client_and_db):
    client, db = client_and_db
    await _seed_strategy_and_backtest(db, drifted=False)

    r = await client.get(f"{_ATTR_BASE}/strategy/st-att/state/1/attribution")
    assert r.status_code == 404  # 尚无归因行(replay 二态:待归因)

    r = await client.post(
        f"{_ATTR_BASE}/strategy/st-att/state/1/attribution",
        json={"category_code": "strategy", "suggested_by": "director"},
    )
    assert r.status_code == 200

    r = await client.get(f"{_ATTR_BASE}/strategy/st-att/state/1/attribution")
    assert r.status_code == 200, r.text
    assert r.json()["attribution"]["category_code"] == "strategy"


def test_router_route_count() -> None:
    paths = [getattr(r, "path", None) for r in missions_router.routes]
    required = (
        "/research/attribution/categories",
        "/research/attribution/stats",
        "/research/strategy/{strategy_id}/state/{episode_n}/attribution/suggest",
        "/research/strategy/{strategy_id}/state/{episode_n}/attribution",
        "/research/strategy/{strategy_id}/state/attribution/rules",
    )
    for p in required:
        assert p in paths, f"missing {p}"
