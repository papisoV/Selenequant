"""Tests for the market-analysis REST endpoint (US-008).

真实符号链路依赖 services/local-api/data/klines.db（缺失时跳过）；
后台任务 / 取消用注入假 flow 验证语义。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import pytest_asyncio
from app.api.v1.research_graph import get_graph_db
from app.core.dependencies import get_market_analysis_service
from app.core.market_analysis_service import MarketAnalysisService
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_market_analysis.data_source import DEFAULT_KLINES_DB, KlineSource
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.skipif(
    not DEFAULT_KLINES_DB.exists(),
    reason="klines.db not present at services/local-api/data",
)

_PROBE = {
    "exchange": "binance",
    "market_type": "futures",
    "symbol": "BTC/USDT",
    "interval": "1h",
}


class _FakeFlow:
    """测试用编排器替身：可注入延迟 / 失败，统计调用次数，可发 on_event 事件。"""

    def __init__(
        self,
        delay: float = 0.0,
        fail: Exception | None = None,
        gate_skip: bool = True,
    ) -> None:
        """gate_skip 默认 True：保持旧替身返回 entry_gate=wait 的语义。"""
        self.delay = delay
        self.fail = fail
        self.gate_skip = gate_skip
        self.calls = 0
        self.incremental_calls = 0
        self.last_prev_record: dict[str, Any] | None = None
        self.last_temperament: str | None = None

    async def analyze(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        on_event: Any = None,
        trade_temperament: str | None = None,
        history_n: int = 0,
    ) -> dict[str, Any]:
        self.calls += 1
        self.last_temperament = trade_temperament
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        if on_event is not None:
            await on_event({"type": "stage1_start", "attempt": 1})
            await on_event({"type": "stage1_text", "delta": "test"})
            await on_event({"type": "stage1_done", "attempt": 1, "usage": {}})
            if self.gate_skip:
                await on_event({"type": "stage2_skipped", "gate": "wait"})
            else:
                await on_event({"type": "stage2_start", "attempt": 1})
                await on_event({"type": "stage2_text", "delta": "test"})
                await on_event({"type": "stage2_done", "attempt": 1, "usage": {}})
        return {
            "meta": {
                "input_id": input_id,
                "session_id": session_id,
                "record_id": "ar-test",
            },
            "input_data": input_data,
            "stage1": {
                "market_regime": "range",
                "entry_gate": "wait" if self.gate_skip else "proceed",
            },
            "stage2": {"decision": "no_trade", "dispatch_playbooks": []},
            "dispatch_playbooks": [],
            "usage_total": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    async def analyze_incremental(
        self,
        input_data: dict[str, Any],
        *,
        prev_record: dict[str, Any] | None,
        input_id: str,
        session_id: str,
        on_event: Any = None,
        trade_temperament: str | None = None,
        history_n: int = 0,
    ) -> dict[str, Any]:
        self.incremental_calls += 1
        self.last_prev_record = prev_record
        return await self.analyze(
            input_data,
            input_id=input_id,
            session_id=session_id,
            on_event=on_event,
            trade_temperament=trade_temperament,
            history_n=history_n,
        )


def _make_app(flow: _FakeFlow):
    app = create_app()
    service = MarketAnalysisService(
        db_loader=lambda: None, settings_loader=lambda: None, flow=flow
    )
    app.dependency_overrides[get_market_analysis_service] = lambda: service
    return app


@pytest.mark.asyncio
async def test_analyze_sync_returns_record():
    flow = _FakeFlow()
    app = _make_app(flow)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/market-analysis/analyze", json=_PROBE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage1"]["entry_gate"] == "wait"
    assert body["meta"]["record_id"] == "ar-test"
    assert flow.calls == 1


@pytest.mark.asyncio
async def test_analyze_missing_symbol_returns_400():
    app = _make_app(_FakeFlow())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/market-analysis/analyze",
            json={"symbol": "NOPE/FAKE", "interval": "1h"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_analyze_sync_maps_flow_errors():
    """同步 /analyze 异常 → 状态码 + error dict（与 SSE analyze_fail 同契约）。"""
    from selene_space_market_analysis.exceptions import CheatError

    flow = _FakeFlow(fail=CheatError(field="bar_features", message="cheat detected"))
    app = _make_app(flow)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/market-analysis/analyze", json=_PROBE)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "cheat_detected"
    assert detail["field"] == "bar_features"


@pytest.mark.asyncio
async def test_background_submit_and_poll():
    flow = _FakeFlow(delay=0.01)
    app = _make_app(flow)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        submit = await client.post(
            "/api/v1/market-analysis/analyze", json={**_PROBE, "background": True}
        )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert job_id.startswith("ma-")
    assert submit.json()["status"] == "running"

    await asyncio.sleep(0.05)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        poll = await client.get(f"/api/v1/market-analysis/analyze/{job_id}")
    assert poll.status_code == 200
    body = poll.json()
    assert body["status"] == "done"
    assert body["result"]["stage1"]["entry_gate"] == "wait"


@pytest.mark.asyncio
async def test_background_cancel():
    flow = _FakeFlow(delay=30.0)
    app = _make_app(flow)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        submit = await client.post(
            "/api/v1/market-analysis/analyze", json={**_PROBE, "background": True}
        )
        job_id = submit.json()["job_id"]
        cancel = await client.delete(f"/api/v1/market-analysis/analyze/{job_id}")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelling"

    await asyncio.sleep(0.05)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        poll = await client.get(f"/api/v1/market-analysis/analyze/{job_id}")
    assert poll.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404():
    app = _make_app(_FakeFlow())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market-analysis/analyze/ma-nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# US-302 Free Chat / US-304 回放 / 导出 / 通知 端点（用假 service 验证语义）
# ---------------------------------------------------------------------------

_RECORD = {
    "meta": {"record_id": "ar-e2e", "timestamp": "2026-08-12T00:00:00Z"},
    "input_data": {
        "exchange": "binance",
        "market_type": "futures",
        "symbol": "BTC/USDT",
        "interval": "1h",
        "klines": [
            {
                "timestamp": 1,
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 1.5,
                "volume": 10,
            },
            {
                "timestamp": 2,
                "open": 1.5,
                "high": 3,
                "low": 1,
                "close": 2,
                "volume": 20,
            },
        ],
    },
    "stage1": {"market_regime": "trend_up", "entry_gate": "proceed"},
    "stage2": {"decision": "market", "dispatch_playbooks": []},
}


class _FakeService:
    """假 MarketAnalysisService：覆盖 chat / records / export / notify 语义。"""

    def __init__(self) -> None:
        self.chat_raises: Exception | None = None

    async def chat(self, record_id: str, message: str) -> dict[str, Any]:
        if self.chat_raises is not None:
            raise self.chat_raises
        return {
            "record_id": record_id,
            "reply": "ok",
            "messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": "ok"},
            ],
        }

    async def chat_history(self, record_id: str) -> dict[str, Any]:
        return {"record_id": record_id, "messages": []}

    async def list_records(
        self, *, symbol: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return [_RECORD]

    async def get_record(self, record_id: str) -> dict[str, Any] | None:
        return _RECORD if record_id == "ar-e2e" else None


def _make_app_svc(svc: Any):
    app = create_app()
    app.dependency_overrides[get_market_analysis_service] = lambda: svc
    return app


@pytest.mark.asyncio
async def test_chat_returns_reply_and_messages():
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/market-analysis/chat",
            json={"record_id": "ar-e2e", "message": "为什么看多？"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_id"] == "ar-e2e"
    assert body["reply"] == "ok"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_chat_unknown_record_maps_to_404():
    svc = _FakeService()
    svc.chat_raises = KeyError("record 'ar-nope' not found")
    app = _make_app_svc(svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/market-analysis/chat",
            json={"record_id": "ar-nope", "message": "hi"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_invalid_message_maps_to_400():
    svc = _FakeService()
    svc.chat_raises = ValueError("empty message")
    app = _make_app_svc(svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/market-analysis/chat",
            json={"record_id": "ar-e2e", "message": "   "},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_chat_history_returns_messages():
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market-analysis/chat/ar-e2e")
    assert resp.status_code == 200
    assert resp.json()["record_id"] == "ar-e2e"


@pytest.mark.asyncio
async def test_records_list_and_get():
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        listed = await client.get("/api/v1/market-analysis/records")
        got = await client.get("/api/v1/market-analysis/records/ar-e2e")
    assert listed.status_code == 200
    assert listed.json()["records"][0]["meta"]["record_id"] == "ar-e2e"
    assert got.status_code == 200
    assert got.json()["meta"]["record_id"] == "ar-e2e"


@pytest.mark.asyncio
async def test_get_missing_record_returns_404():
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market-analysis/records/ar-nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /records/forecast-eval — 预测校准审计
# ---------------------------------------------------------------------------


class _FakeEvalService:
    """只依赖 list_records 的鸭子类型替身（端点不依赖其它成员）。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    async def list_records(
        self, *, symbol: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self.records


def _forecast_record(
    rid: str,
    last_ts: int,
    last_close: float,
    direction: str | None,
    probability: float | None,
) -> dict[str, Any]:
    return {
        "meta": {"record_id": rid},
        "input_data": {
            "exchange": "binance",
            "market_type": "futures",
            "symbol": "BTC/USDT",
            "interval": "1h",
            "klines": [
                {
                    "timestamp": last_ts,
                    "open": last_close,
                    "high": last_close,
                    "low": last_close,
                    "close": last_close,
                    "volume": 1,
                }
            ],
        },
        "stage1": {"market_regime": "range", "entry_gate": "proceed"},
        "stage2": {
            "decision": "market",
            "dispatch_playbooks": [],
            "next_bar_forecast": {
                "direction": direction,
                "probability": probability,
                "note": "audit",
            },
        },
    }


@pytest.mark.asyncio
async def test_forecast_eval_endpoint():
    """真实 klines.db 提供后继收盘；1 命中 1 未中 + 1 neutral（排除）。"""
    df = KlineSource().load("binance", "futures", "BTC/USDT", "1h", limit=500)
    ts = list(df["timestamp"])
    closes = list(df["close"])
    last_ts = ts[-2]
    last_close = float(closes[-2])
    next_close = float(closes[-1])
    actual = (
        "up"
        if next_close > last_close
        else "down"
        if next_close < last_close
        else "flat"
    )
    opposite = "down" if actual == "up" else "up"

    records = [
        _forecast_record("eval-hit", last_ts, last_close, actual, 0.6),
        _forecast_record("eval-miss", last_ts, last_close, opposite, 0.4),
        _forecast_record("eval-neutral", last_ts, last_close, "neutral", None),
    ]
    app = _make_app_svc(_FakeEvalService(records))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/market-analysis/records/forecast-eval?symbol=BTC/USDT&interval=1h"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["interval"] == "1h"
    assert body["samples_total"] == 3
    assert body["samples_with_forecast"] == 2
    assert body["hit_rate_forecast"] == pytest.approx(0.5)
    assert body["hit_rate_baseline"] is not None
    rows = {r["record_id"]: r for r in body["rows"]}
    assert rows["eval-hit"]["forecast_hit"] is True
    assert rows["eval-hit"]["probability"] == 0.6
    assert rows["eval-miss"]["forecast_hit"] is False
    # neutral 不在 _VALID_DIRECTIONS → 预测为空，只占样本总数不占预测样本
    assert rows["eval-neutral"]["forecast"] is None


# ---------------------------------------------------------------------------
# /records/ic-panel + /records/return-report — 跨截面 IC / 多空回报面板
# ic-panel 走快照契约:GET 只读快照,POST mine 实时算+落库(唯一写路径)。
# 快照读写的 db 用内存库注入(get_graph_db override);mine 的实时截面仍依赖
# 真实 klines.db 的 BTC/ETH/SOL(模块级 skip 已覆盖 db 缺失)。
# ---------------------------------------------------------------------------

_CROSS = "BTC/USDT,ETH/USDT,SOL/USDT"


@pytest_asyncio.fixture
async def snap_db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


def _make_app_snap(snap_db_: Any):
    """ic-panel 快照端点专用 app:内存库注入,svc 替身保持模块一致性。"""
    app = _make_app_svc(_FakeService())
    app.dependency_overrides[get_graph_db] = lambda: snap_db_
    return app


@pytest.mark.asyncio
async def test_ic_panel_no_snapshot_empty_structure(snap_db):
    """无快照 → 200 + no_snapshot + 空 series 结构(零 KlineSource 零模拟)。"""
    app = _make_app_snap(snap_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/market-analysis/records/ic-panel?symbols={_CROSS}&interval=1h"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_snapshot"
    assert body["symbols"] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert body["rolling_ic"] == {"dates": [], "values": []}
    assert body["ic_distribution"] == {}
    assert body["monthly_heatmap"] == {"years": [], "months": [], "z": []}
    assert body["sector_ic"] == {"groups": [], "contribs": []}


@pytest.mark.asyncio
async def test_ic_panel_mine_then_get(snap_db):
    """POST mine 落库 → GET 从快照重算:确定性数值一致 + 原字段契约 + 快照元信息。"""
    app = _make_app_snap(snap_db)
    mine_params = {
        "symbols": _CROSS,
        "interval": "1h",
        "period": 5,
        "limit": 300,
        "rolling_window": 20,
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        mine = await client.post(
            "/api/v1/market-analysis/records/ic-panel/mine", json=mine_params
        )
        assert mine.status_code == 200, mine.text
        mine_body = mine.json()
        get_resp = await client.get(
            "/api/v1/market-analysis/records/ic-panel?symbols=BTC%2FUSDT%2CETH%2FUSDT%2CSOL%2FUSDT&interval=1h&limit=300"
        )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "ok"
    # mine→get 数值一致(ICPanel 纯函数,同快照同输出)
    assert body["rolling_ic"] == mine_body["rolling_ic"]
    assert body["rolling_ir"] == mine_body["rolling_ir"]
    assert body["ic_distribution"] == mine_body["ic_distribution"]
    assert body["autocorr"] == mine_body["autocorr"]
    # 原字段契约不变
    assert body["symbols"] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert body["interval"] == "1h"
    assert body["factor"] == "momentum"
    assert body["period"] == 5
    assert body["rolling_ic"]["dates"]
    assert len(body["rolling_ic"]["dates"]) == len(body["rolling_ic"]["values"])
    # 快照元信息
    assert body["snapshot"]["episode_id"] == mine_body["snapshot"]["episode_id"]
    assert body["snapshot"]["boundary_end"]
    assert body["snapshot"]["n_rows"] == mine_body["snapshot"]["n_rows"] > 0


@pytest.mark.asyncio
async def test_ic_panel_rolling_window_reparameterizes_from_snapshot(snap_db):
    """rolling_window 是消费方参数:同一快照 GET 换窗口,衍生序列随之变化。"""
    app = _make_app_snap(snap_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        mine = await client.post(
            "/api/v1/market-analysis/records/ic-panel/mine",
            json={"symbols": _CROSS, "interval": "1h", "limit": 300},
        )
        assert mine.status_code == 200
        r20 = await client.get(
            "/api/v1/market-analysis/records/ic-panel?symbols=BTC%2FUSDT%2CETH%2FUSDT%2CSOL%2FUSDT&interval=1h&limit=300&rolling_window=20"
        )
        r60 = await client.get(
            "/api/v1/market-analysis/records/ic-panel?symbols=BTC%2FUSDT%2CETH%2FUSDT%2CSOL%2FUSDT&interval=1h&limit=300&rolling_window=60"
        )
    assert r20.status_code == 200 and r60.status_code == 200
    b20, b60 = r20.json(), r60.json()
    assert b20["status"] == "ok" and b60["status"] == "ok"
    assert b20["snapshot"]["episode_id"] == b60["snapshot"]["episode_id"]
    # rolling_window 只进 rolling_ir(ic_panel.py:189-190);rolling_ic 是逐日序列不受窗口影响
    assert b20["rolling_ir"] != b60["rolling_ir"]
    assert b20["rolling_ic"] == b60["rolling_ic"]


@pytest.mark.asyncio
async def test_ic_panel_mine_key_normalizes_and_misses_on_param_change(snap_db):
    """symbols 乱序 mine 覆盖同一快照;改 period → 不同 key → no_snapshot。"""
    app = _make_app_snap(snap_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/v1/market-analysis/records/ic-panel/mine",
            json={
                "symbols": "SOL/USDT,ETH/USDT,BTC/USDT",
                "interval": "1h",
                "limit": 300,
            },
        )
        assert first.status_code == 200
        reshuffled = await client.post(
            "/api/v1/market-analysis/records/ic-panel/mine",
            json={"symbols": _CROSS, "interval": "1h", "limit": 300},
        )
        assert reshuffled.status_code == 200
        get_resp = await client.get(
            "/api/v1/market-analysis/records/ic-panel?symbols=BTC%2FUSDT%2CETH%2FUSDT%2CSOL%2FUSDT&interval=1h&limit=300"
        )
        assert (
            get_resp.json()["snapshot"]["episode_id"]
            == reshuffled.json()["snapshot"]["episode_id"]
        )
        # 改 period → 新 key → 无快照
        miss = await client.get(
            "/api/v1/market-analysis/records/ic-panel?symbols=BTC%2FUSDT%2CETH%2FUSDT%2CSOL%2FUSDT&interval=1h&limit=300&period=10"
        )
    assert miss.status_code == 200
    assert miss.json()["status"] == "no_snapshot"


@pytest.mark.asyncio
async def test_ic_panel_mine_bad_question_returns_404(snap_db):
    """anchor fail-fast:question 不存在 → 404(且不落库)。"""
    app = _make_app_snap(snap_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/market-analysis/records/ic-panel/mine",
            json={"symbols": _CROSS, "question_id": "q-not-exist"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_return_report_endpoint():
    """跨截面权重漂移回测 → 累计 LS 回报 / 换手 / 统计。"""
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/market-analysis/records/return-report?symbols={_CROSS}&interval=1h&period=5&bins=5"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_bins"] == 5
    assert len(body["cum_ret_ls"]["dates"]) >= 1
    assert len(body["cum_ret_net_ls"]["dates"]) >= 1
    assert body["turnover_ls"]["dates"]  # 至少一个换手观测
    assert "Ann. Sharpe" in body["stats_ls"]


@pytest.mark.asyncio
async def test_ic_panel_unsupported_factor_returns_400(snap_db):
    app = _make_app_snap(snap_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market-analysis/records/ic-panel?factor=nope")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ic_panel_empty_symbols_returns_400(snap_db):
    app = _make_app_snap(snap_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market-analysis/records/ic-panel?symbols=")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_return_report_unknown_symbols_returns_502():
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/market-analysis/records/return-report?symbols=NOPE/FAKE"
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_export_csv_returns_download():
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market-analysis/records/ar-e2e/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="ar-e2e.csv"'
    assert resp.text.startswith("timestamp,open,high,low,close,volume")
    assert "1,1,2,0,1.5,10" in resp.text  # 首根 K 线行
    assert "2,1.5,3,1,2,20" in resp.text  # 次根 K 线行


@pytest.mark.asyncio
async def test_notify_disabled_when_env_off(monkeypatch):
    monkeypatch.delenv("SELENE_ANALYSIS_NOTIFY_ENABLED", raising=False)
    app = _make_app_svc(_FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/market-analysis/notify", json={"record_id": "ar-e2e"}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


# ---------------------------------------------------------------------------
# US-002 SSE 流式端点：analyze_start → stage1_* → (stage2_skipped|stage2_*) → analyze_done
# ---------------------------------------------------------------------------


async def _read_sse_events(app, payload):
    """POST /analyze/stream 并解析全部 SSE data 事件（dict 列表）。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with client.stream(
            "POST", "/api/v1/market-analysis/analyze/stream", json=payload
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            data = await resp.aread()
    events: list[dict] = []
    for frame in data.decode().split("\n\n"):
        line = frame.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


@pytest.mark.asyncio
async def test_analyze_stream_emits_full_event_sequence():
    flow = _FakeFlow(gate_skip=False)
    app = _make_app(flow)
    events = await _read_sse_events(app, _PROBE)
    types = [e["type"] for e in events]
    assert types[0] == "analyze_start"
    assert types[-1] == "analyze_done"
    assert "stage1_start" in types
    assert "stage1_text" in types
    assert "stage1_done" in types
    assert "stage2_start" in types
    assert "stage2_text" in types
    assert "stage2_done" in types
    assert "stage2_skipped" not in types
    assert events[-1]["record"]["meta"]["record_id"] == "ar-test"
    assert flow.calls == 1


@pytest.mark.asyncio
async def test_analyze_stream_gate_short_circuit_emits_skipped():
    flow = _FakeFlow(gate_skip=True)
    app = _make_app(flow)
    events = await _read_sse_events(app, _PROBE)
    types = [e["type"] for e in events]
    assert "stage2_skipped" in types
    assert "stage2_start" not in types
    assert types[-1] == "analyze_done"
    assert events[-1]["record"]["stage1"]["entry_gate"] == "wait"


@pytest.mark.asyncio
async def test_analyze_stream_failure_emits_analyze_fail():
    from selene_space_market_analysis.exceptions import CheatError

    flow = _FakeFlow(fail=CheatError(field="bar_features", message="cheat detected"))
    app = _make_app(flow)
    events = await _read_sse_events(app, _PROBE)
    types = [e["type"] for e in events]
    assert types[0] == "analyze_start"
    assert types[-1] == "analyze_fail"
    assert events[-1]["error"]["error"] == "cheat_detected"
    assert events[-1]["error"]["field"] == "bar_features"


@pytest.mark.asyncio
async def test_analyze_stream_forwards_trade_temperament():
    flow = _FakeFlow()
    app = _make_app(flow)
    await _read_sse_events(app, {**_PROBE, "trade_temperament": "aggressive"})
    assert flow.last_temperament == "aggressive"


@pytest.mark.asyncio
async def test_analyze_stream_forwards_incremental():
    flow = _FakeFlow()
    app = _make_app(flow)
    await _read_sse_events(app, {**_PROBE, "incremental": True})
    assert flow.incremental_calls == 1
    assert flow.last_prev_record is None  # 注入 flow 无持久化 → 退化为无先验


# ---------------------------------------------------------------------------
# US-005 服务层：incremental=True 时查找最近同 symbol+interval 成功记录作为先验
# ---------------------------------------------------------------------------


class _StubPersistence:
    """假持久化：list_recent 返回预置记录（复用 _find_prev_record 过滤逻辑）。"""

    def __init__(self, records: list[dict]) -> None:
        self.records = records

    async def list_recent(
        self, *, symbol: str | None = None, limit: int = 50
    ) -> list[dict]:
        return self.records


@pytest.mark.asyncio
async def test_incremental_finds_prev_record_and_passes_it():
    prev = {
        "meta": {"record_id": "ar-prev"},
        "stage1": {
            "market_regime": "trend_up",
            "entry_gate": "proceed",
            "diagnosis_summary": {"bias": "bullish"},
        },
        "input_data": {"interval": "1h"},
    }
    flow = _FakeFlow()
    svc = MarketAnalysisService(
        db_loader=lambda: None, settings_loader=lambda: None, flow=flow
    )
    svc._persistence = _StubPersistence([prev])  # noqa: SLF001 测试直接注入假持久化
    input_data = {
        "exchange": "binance",
        "market_type": "futures",
        "symbol": "BTC/USDT",
        "interval": "1h",
        "klines": [],
    }
    await svc.analyze(input_data, input_id="in-1", session_id="s-1", incremental=True)
    assert flow.incremental_calls == 1
    assert flow.last_prev_record == prev


@pytest.mark.asyncio
async def test_incremental_ignores_exception_and_wrong_interval_records():
    flow = _FakeFlow()
    svc = MarketAnalysisService(
        db_loader=lambda: None, settings_loader=lambda: None, flow=flow
    )
    svc._persistence = _StubPersistence(  # noqa: SLF001
        [
            {
                "meta": {"record_id": "ar-exc"},
                "exception": {"type": "X", "message": "boom"},
                "input_data": {"interval": "1h"},
            },
            {
                "meta": {"record_id": "ar-other"},
                "stage1": {"diagnosis_summary": {}},
                "input_data": {"interval": "4h"},
            },
        ]
    )
    input_data = {
        "exchange": "binance",
        "market_type": "futures",
        "symbol": "BTC/USDT",
        "interval": "1h",
        "klines": [],
    }
    await svc.analyze(input_data, input_id="in-2", session_id="s-2", incremental=True)
    assert flow.incremental_calls == 1
    assert flow.last_prev_record is None  # 无匹配成功记录 → 先验为 None
