"""Backtest endpoints: task lifecycle, shared result store, listing filters."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import app.api.v1.backtests as backtests_mod
import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_types.strategy import BacktestMetrics, BacktestResult


@pytest.fixture(autouse=True)
def _wipe_tasks():
    backtests_mod._tasks.clear()
    yield
    backtests_mod._tasks.clear()


def _sample_result(
    id: str = "bt-001", strategy_id: str = "strat-001"
) -> BacktestResult:
    metrics = dict(
        recovery_factor=1.9,
        expectancy=12.3,
        sortino_ratio=2.1,
        calmar_ratio=5.6,
        total_trades=42,
        profit_factor=1.6,
        win_rate=0.55,
        max_drawdown=-0.08,
        sharpe_ratio=1.8,
        annual_return=0.45,
        total_return=0.15,
    )
    return BacktestResult(
        trades_count=1,
        created_at=1711000000000,
        trades=[{"pnl": 10.0, "side": "buy", "timestamp": 1711000000000}],
        drawdown_curve=[0.0, 0.0, -0.02],
        equity_curve=[10000.0, 10100.0, 10500.0],
        metrics=BacktestMetrics(**metrics),  # type: ignore[arg-type]
        end_date=datetime(2025, 3, 31, tzinfo=UTC),
        start_date=datetime(2025, 1, 1, tzinfo=UTC),
        strategy_id=strategy_id,
        id=id,
    )


@asynccontextmanager
async def api(lenient: bool = False):
    opts: dict[str, object] = {} if not lenient else {"raise_app_exceptions": False}
    async with AsyncClient(
        transport=ASGITransport(app=create_app(), **opts),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        yield c


async def _queue(c: AsyncClient, strategy_id: str, **extra) -> str:
    made = await c.post("/api/v1/backtests", json={"strategy_id": strategy_id, **extra})
    assert made.status_code == 202
    task_id = made.json()["data"]["task_id"]
    assert isinstance(task_id, str)
    return task_id


@pytest.mark.asyncio
async def test_start_returns_202_with_pending_task():
    async with api() as c:
        started = await c.post(
            "/api/v1/backtests",
            json={"initial_cash": 10000, "strategy_id": "strat-abc"},
        )
    assert started.status_code == 202
    body = started.json()
    assert body["data"]["task_id"].startswith("bt-")
    assert body["data"]["status"] == "pending"
    assert body["meta"]["request_id"] == started.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_unknown_strategy_fails_the_task():
    async with api() as c:
        task_id = await _queue(c, "strat-abc")
        await asyncio.sleep(0.1)
        polled = await c.get(f"/api/v1/backtests/{task_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["data"]["status"] == "failed"
    assert body["data"].get("result") is None
    assert "error" in body["data"]


@pytest.mark.asyncio
async def test_get_unknown_task_errors_out():
    async with api(lenient=True) as c:
        resp = await c.get("/api/v1/backtests/nonexistent")
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_listing_counts_every_started_task():
    async with api() as c:
        for sid in ("s1", "s2", "s1"):
            await _queue(c, sid)
        listed = await c.get("/api/v1/backtests")
    assert listed.status_code == 200
    assert listed.json()["meta"]["pagination"]["total"] == 3


@pytest.mark.asyncio
async def test_shared_agent_store_result_is_served():
    from selene_space_agent.tools.strategy_tools import get_results_store

    store = get_results_store()
    store.clear()
    seeded = _sample_result("bt-shared", "strat-shared")
    store[seeded.id] = seeded
    try:
        async with api() as c:
            resp = await c.get("/api/v1/backtests/bt-shared")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "completed"
        assert data["annual_return"] == 0.45
        assert data["drawdown_curve"] == [0.0, 0.0, -0.02]
        assert len(data["trades"]) == 1
        assert data["result"]["metrics"]["calmar_ratio"] == 5.6
    finally:
        store.clear()


@pytest.mark.asyncio
async def test_detail_exposes_config_snapshot_and_lookahead():
    from selene_space_agent.tools.strategy_tools import get_results_store

    store = get_results_store()
    store.clear()
    seeded = _sample_result("bt-snap", "strat-snap")
    # 老结果(无 config_snapshot)也必须能出 — 默认空 dict
    store[seeded.id] = seeded
    try:
        async with api() as c:
            resp = await c.get("/api/v1/backtests/bt-snap")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["config_snapshot"] == {}
        assert data["lookahead_warnings"] == []
    finally:
        store.clear()

    # 带 config_snapshot 的结果原样透传
    seeded.config_snapshot = {
        "initial_capital": 10_000.0,
        "commission_rate": 0.001,
        "slippage_bps": 5.0,
        "fill_rule": "next_bar_open",
    }
    seeded.lookahead_warnings = ["w1"]
    store[seeded.id] = seeded
    try:
        async with api() as c:
            resp = await c.get("/api/v1/backtests/bt-snap")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["config_snapshot"]["fill_rule"] == "next_bar_open"
        assert data["lookahead_warnings"] == ["w1"]
    finally:
        store.clear()


@pytest.mark.asyncio
async def test_shared_store_rows_appear_in_listing():
    from selene_space_agent.tools.strategy_tools import get_results_store

    store = get_results_store()
    store.clear()
    seeded = _sample_result("bt-shared-list", "s1")
    store[seeded.id] = seeded
    try:
        async with api() as c:
            listed = await c.get("/api/v1/backtests?strategy_id=s1")
        assert listed.status_code == 200
        rows = listed.json()["data"]
        assert len(rows) == 1
        assert rows[0]["id"] == "bt-shared-list"
        assert rows[0]["status"] == "completed"
    finally:
        store.clear()


@pytest.mark.asyncio
async def test_strategy_filter_narrows_listing():
    async with api() as c:
        await _queue(c, "s2")
        await _queue(c, "s1")
        listed = await c.get("/api/v1/backtests?strategy_id=s1")
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == "s1"


@pytest.mark.asyncio
async def test_slippage_bps_accepted_on_start():
    async with api() as c:
        started = await c.post(
            "/api/v1/backtests",
            json={"strategy_id": "strat-slip", "slippage_bps": 5.0},
        )
    assert started.status_code == 202


# ---------------------------------------------------------------------------
# DELETE /backtests/{id}
#
# 回归:装饰器一度挂在辅助函数 ``_require_backtest_owner`` 上(签名含
# ``user_id: str``),FastAPI 把 user_id 当必填 query 参 → 端点对任何请求都回
# 422 "Field required",真正的处理器从未注册。以下两条锁住"能删"与"删完不再
# 出现",防止装饰器再被挂错。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_task_from_listing():
    async with api() as c:
        task_id = await _queue(c, "strat-del")
        listed = await c.get("/api/v1/backtests")
        assert any(r["id"] == task_id for r in listed.json()["data"])

        resp = await c.delete(f"/api/v1/backtests/{task_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["deleted"] == task_id

        after = await c.get("/api/v1/backtests")
        assert all(r["id"] != task_id for r in after.json()["data"])


@pytest.mark.asyncio
async def test_delete_missing_id_does_not_require_query_param():
    """删除不存在的 id 也不该 422 —— 422 说明端点又变回了辅助函数。"""
    async with api() as c:
        resp = await c.delete("/api/v1/backtests/bt-nonexistent-probe")
    assert resp.status_code != 422, resp.text


@pytest.mark.asyncio
async def test_detail_exposes_robustness_block():
    """稳健性随详情透传;老结果(无该字段)读回空 dict,不是 None。

    空 dict 与"跑过但全拒"必须可区分:前者是"没算",后者每个检查带 error 键。
    """
    from selene_space_agent.tools.strategy_tools import get_results_store

    store = get_results_store()
    store.clear()
    seeded = _sample_result("bt-rob", "strat-rob")
    # 老结果没有 robustness —— 默认空 dict,前端据此渲染"未计算"
    store[seeded.id] = seeded
    try:
        async with api() as c:
            resp = await c.get("/api/v1/backtests/bt-rob")
        assert resp.status_code == 200
        assert resp.json()["data"]["robustness"] == {}
    finally:
        store.clear()

    seeded.robustness = {
        "permutation": {
            "p_value_sharpe": 0.008,
            "n_trades": 420,
            "p_value_floor": 0.001,
        },
        "bootstrap": {"ci_lower": 0.4, "ci_upper": 2.1, "prob_positive": 0.97},
        "seed": 42,
    }
    store[seeded.id] = seeded
    try:
        async with api() as c:
            resp = await c.get("/api/v1/backtests/bt-rob")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["robustness"]["permutation"]["p_value_sharpe"] == 0.008
        assert data["robustness"]["bootstrap"]["ci_upper"] == 2.1
        assert data["robustness"]["seed"] == 42
    finally:
        store.clear()


@pytest.mark.asyncio
async def test_start_accepts_robustness_flag():
    """请求体接受 robustness 开关(默认 True);关掉仅为免跑,不影响发车。"""
    async with api(lenient=True) as c:
        resp = await c.post(
            "/api/v1/backtests",
            json={"strategy_id": "bb_v3", "symbol": "BTC/USDT", "robustness": False},
        )
        # 策略不存在/无数据都可能失败,但校验层必须接受该字段 —— 不是 422
        assert resp.status_code != 422, resp.text
