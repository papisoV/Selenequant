"""Tests for POST /api/v1/setup-backtest.

用假 kline manager（依赖覆盖注入）提供合成 K 线，验证端点语义：
- 已知 entry 触发 N 个 setup → 聚合指标正确
- 无触发 → 200 空样本（total_setups=0 + reason，不崩溃）
- 无效参数（SL==TP / 方向矛盾）→ 422
- K 线拉取失败 → 502；无数据 → 400
- lookback_bars 截断生效
- T2a: input_id 非空 → 结果挂诊断记录；挂载失败不失败请求
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
import pytest_asyncio
from app.core.dependencies import (
    get_kline_manager,
    set_decision_workflow_service,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient


class _FakeKlineManager:
    """返回固定 K 线的替身；可注入异常 / 空 df。"""

    def __init__(
        self, df: pd.DataFrame | None = None, exc: Exception | None = None
    ) -> None:
        self.df = df
        self.exc = exc
        self.last_kwargs: dict[str, Any] = {}

    async def get_klines(self, **kwargs: Any) -> pd.DataFrame:
        self.last_kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return self.df if self.df is not None else pd.DataFrame()


def _klines(rows: list[tuple[int, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [1000.0] * len(rows),
        }
    )


_BASE: list[tuple[int, float, float, float, float]] = [
    (1, 102, 105, 100, 104),  # trigger，未收敛
    (2, 104, 112, 103, 111),  # high>=110 → WIN
    (3, 108, 109, 101, 107),  # 无触发
    (4, 106, 108, 98, 100),  # trigger，未收敛
    (5, 100, 102, 94, 96),  # low<=95 → LOSS
]
# 单周期 5 根含 2 setups（win+loss）；6 周期=30 根过 min_candles=20
_DF = _klines(_BASE * 6)


@pytest_asyncio.fixture
async def client():
    app = create_app()
    fake = _FakeKlineManager(df=_DF)
    app.dependency_overrides[get_kline_manager] = lambda: fake
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.state = fake  # 测试内可访问 fake
        yield c


def _payload(**overrides: Any) -> dict:
    base = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "exchange": "binance",
        "market_type": "futures",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "direction": "long",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_setup_backtest_returns_aggregate(client: AsyncClient):
    resp = await client.post("/api/v1/setup-backtest", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    data = body["data"]
    assert data["symbol"] == "BTC/USDT"
    assert data["timeframe"] == "1h"
    assert data["total_setups"] == 12  # 6 周期 x 2 setups
    assert data["win_rate"] == pytest.approx(0.5)
    assert data["expectancy"] == pytest.approx(0.5)
    assert data["profit_factor"] == pytest.approx(2.0)
    assert len(data["trades"]) == 12
    assert data["no_trigger"] is False


@pytest.mark.asyncio
async def test_kline_manager_called_with_source_auto(client: AsyncClient):
    fake: _FakeKlineManager = getattr(client, "state")
    await client.post("/api/v1/setup-backtest", json=_payload())
    assert fake.last_kwargs["exchange"] == "binance"
    assert fake.last_kwargs["market_type"] == "futures"
    assert fake.last_kwargs["symbol"] == "BTC/USDT"
    assert fake.last_kwargs["interval"] == "1h"
    assert fake.last_kwargs["source"] == "auto"


@pytest.mark.asyncio
async def test_direction_inferred_when_omitted(client: AsyncClient):
    resp = await client.post("/api/v1/setup-backtest", json=_payload(direction=None))
    assert resp.status_code == 200
    assert resp.json()["data"]["total_setups"] == 12


@pytest.mark.asyncio
async def test_no_trigger_returns_empty_sample_200(client: AsyncClient):
    df_no_trigger = _klines([(i, 112, 118, 110, 115) for i in range(1, 31)])
    app = create_app()
    fake = _FakeKlineManager(df=df_no_trigger)
    app.dependency_overrides[get_kline_manager] = lambda: fake
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/setup-backtest", json=_payload())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_setups"] == 0
    assert data["no_trigger"] is True
    assert data["reason"] is not None


@pytest.mark.asyncio
async def test_lookback_bars_truncates(client: AsyncClient):
    resp = await client.post("/api/v1/setup-backtest", json=_payload(lookback_bars=25))
    assert resp.status_code == 200
    # 30 根截到最近 25 根：仍含 5 个周期 x (win+loss) = 10 setups? 不——
    # _BASE 单周期 5 根含 2 触发，25 根 = 5 周期 → 10 setups
    data = resp.json()["data"]
    assert data["total_setups"] == 10


@pytest.mark.asyncio
async def test_invalid_params_returns_422(client: AsyncClient):
    resp = await client.post(
        "/api/v1/setup-backtest", json=_payload(stop_loss=110.0, take_profit=110.0)
    )
    assert resp.status_code == 422
    assert "sl" in resp.json()["detail"] or "stop_loss" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_direction_price_mismatch_returns_422(client: AsyncClient):
    resp = await client.post(
        "/api/v1/setup-backtest", json=_payload(stop_loss=110.0, take_profit=120.0)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_kline_fetch_failure_returns_502(client: AsyncClient):
    app = create_app()
    fake = _FakeKlineManager(exc=RuntimeError("boom"))
    app.dependency_overrides[get_kline_manager] = lambda: fake
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/setup-backtest", json=_payload())
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_empty_klines_returns_400(client: AsyncClient):
    app = create_app()
    fake = _FakeKlineManager(df=pd.DataFrame())
    app.dependency_overrides[get_kline_manager] = lambda: fake
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/setup-backtest", json=_payload())
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# T2a — input_id 审计链: 结果挂到诊断记录 (W2)
# ---------------------------------------------------------------------------


class _AttachRecorder:
    """决策服务替身：记录 attach_backtest 调用，可注入返回值/异常。"""

    def __init__(self, result: bool = True, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result
        self._exc = exc

    async def attach_backtest(self, input_id: str, payload: dict[str, Any]) -> bool:
        self.calls.append((input_id, payload))
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest_asyncio.fixture
async def attach_recorder():
    rec = _AttachRecorder()
    set_decision_workflow_service(rec)
    yield rec
    set_decision_workflow_service(None)


@pytest.mark.asyncio
async def test_input_id_attaches_backtest_to_record(
    client: AsyncClient, attach_recorder: _AttachRecorder
):
    """带 input_id POST → service.attach_backtest 被调（含瘦 payload），响应标记 attached。"""
    resp = await client.post(
        "/api/v1/setup-backtest", json=_payload(input_id="w2-t2a-1")
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["attached_to_record"] is True
    assert len(attach_recorder.calls) == 1
    input_id, attached_payload = attach_recorder.calls[0]
    assert input_id == "w2-t2a-1"
    assert attached_payload["symbol"] == "BTC/USDT"
    assert attached_payload["total_setups"] == 12
    assert attached_payload["source"] == "setup_backtest"
    # 瘦 payload：不携带全量 trades 明细
    assert "trades" not in attached_payload


@pytest.mark.asyncio
async def test_no_input_id_skips_attach(
    client: AsyncClient, attach_recorder: _AttachRecorder
):
    """无 input_id → attach 不被调，响应 attached=False，回测照常。"""
    resp = await client.post("/api/v1/setup-backtest", json=_payload())
    assert resp.status_code == 200
    assert resp.json()["data"]["attached_to_record"] is False
    assert attach_recorder.calls == []


@pytest.mark.asyncio
async def test_attach_repo_miss_returns_200(
    client: AsyncClient, attach_recorder: _AttachRecorder
):
    """repo 未命中 input_id（attach_backtest False）→ 回测仍 200，不重试。"""
    attach_recorder._result = False
    resp = await client.post(
        "/api/v1/setup-backtest", json=_payload(input_id="missing-id")
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["attached_to_record"] is False
    assert data["total_setups"] == 12  # 回测结果照常返回
    assert len(attach_recorder.calls) == 1  # False 不重试


@pytest.mark.asyncio
async def test_attach_service_error_returns_200(
    client: AsyncClient, attach_recorder: _AttachRecorder
):
    """service 抛异常（乐观锁炸/db 崩）→ warning 路径，回测仍 200。"""
    attach_recorder._exc = RuntimeError("lock contention")
    resp = await client.post(
        "/api/v1/setup-backtest", json=_payload(input_id="w2-boom")
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["attached_to_record"] is False
    assert resp.json()["data"]["total_setups"] == 12


@pytest.mark.asyncio
async def test_attach_survives_service_restart_readback(client: AsyncClient):
    """验收①的跨重启读回半边：真实 service + 真实 db，端点挂载后新 service 读回。

    （完整重启读回在 persistence 层已由 test_attach_backtest_refreshes_cache_and_db
    覆盖；此处验证端点→真实 service 的接线路径，不只命中替身。）
    """
    from app.core.decision_workflow_service import DecisionWorkflowService
    from selene_space_storage.migrations import MigrationRunner
    from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
    from selene_space_storage.sqlite import AsyncSQLiteManager

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        svc = DecisionWorkflowService(db_loader=lambda: db)
        seed = svc.analyze(
            "trading_decision",
            {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
            input_id="w2-t2a-restart",
            session_id="s1",
        )
        await svc._persist_record(seed)
        set_decision_workflow_service(svc)
        try:
            resp = await client.post(
                "/api/v1/setup-backtest", json=_payload(input_id="w2-t2a-restart")
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["attached_to_record"] is True

            # "重启"：新 service 实例，同 db — cache-first 读路径必须读得到挂载键。
            restarted = DecisionWorkflowService(db_loader=lambda: db)
            got = await restarted.get_record("w2-t2a-restart")
            assert got is not None
            assert got.backtest_verification is not None
            assert got.backtest_verification["symbol"] == "BTC/USDT"
        finally:
            set_decision_workflow_service(None)
