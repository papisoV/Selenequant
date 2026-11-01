"""POST /analyze-trading htf_interval parameter — endpoint-side HTF aggregation.

The endpoint owns the HTF fetch + aggregation (frontend only names the
interval): it pulls the HTF series from the same klines.db via
trading_pipeline.load_bars(min_bars=0) and passes the pre-aggregated
htf_summary dict to the service. A thin HTF series degrades to
htf_summary_data=None (CLI parity with run_pa_diagnosis B2 branch) — it
never aborts the primary diagnosis.

All tests are hermetic: load_bars is monkeypatched (function-level import
in the handler resolves the patched attribute at request time) and the
klines-db presence gate points at a temp file.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.core.dependencies import get_decision_workflow_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_decision_workflow.trading_pipeline import (
    htf_summary as real_htf_summary,
)

_TP_MOD = "selene_space_decision_workflow.trading_pipeline"


def _bars(n: int, interval_seconds: int = 3600) -> list[dict[str, Any]]:
    """Deterministic synthetic bars, oldest-first, ts ascending."""
    base, step, start_ts = 100.0, 0.5, 1_700_000_000
    return [
        {
            "ts": start_ts + i * interval_seconds,
            "open": base + i * step,
            "high": base + i * step + 1.0,
            "low": base + i * step - 1.0,
            "close": base + i * step + 0.25,
            "volume": 10.0 + i,
        }
        for i in range(n)
    ]


class _FakeLoader:
    """trading_pipeline.load_bars 替身：按 interval 分发预置序列并记录调用。"""

    def __init__(self, series: dict[str, list[dict[str, Any]]]) -> None:
        self.series = series
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        db_path: Any,
        symbol: str,
        interval: str,
        bars: int,
        market_type: str = "futures",
        *,
        min_bars: int = 50,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "bars": bars,
                "market_type": market_type,
                "min_bars": min_bars,
            }
        )
        rows = self.series.get(interval, [])
        # Real contract: raise RuntimeError("insufficient bars…") below
        # min_bars; min_bars=0 (HTF) disables the check entirely.
        if len(rows) < min_bars:
            raise RuntimeError(f"insufficient bars in {db_path}: {len(rows)}")
        return list(rows)


class _StubDump:
    """model_dump() 鸭子类型替身（handler 只调 model_dump）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return self._data


class _FakeDWService:
    """DecisionWorkflowService.analyze_trading 替身：捕获 htf_summary_data。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def analyze_trading(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        trading_stance: str | None = None,
        htf_summary_data: dict[str, Any] | None = None,
        direction_signal: dict[str, Any] | None = None,
        demo: bool = False,
    ) -> Any:
        self.calls.append(
            {
                "input_data": input_data,
                "input_id": input_id,
                "session_id": session_id,
                "trading_stance": trading_stance,
                "htf_summary_data": htf_summary_data,
                "demo": demo,
            }
        )
        return SimpleNamespace(
            meta=_StubDump({"input_id": input_id, "record_id": "dw-htf-test"}),
            input_data=input_data,
            stage1=_StubDump({"decision_summary": {"bias": "neutral"}}),
            stage2=_StubDump({"decision": "wait"}),
            strategy_files_used=[],
            usage_total={"prompt_tokens": 1, "completion_tokens": 1},
            # 审计键 — AnalysisRecord 必带三个可选字段,stub 须同步,
            # 否则 _to_response 投影时 AttributeError。
            outcome=None,
            backtest_verification=None,
            followups=None,
        )


def _make_app(
    svc: _FakeDWService,
    loader: _FakeLoader,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    db: bool = True,
):
    """create_app + 依赖覆盖：假 service + 假 load_bars + 隔离 klines.db 门。"""
    import selene_space_decision_workflow.trading_pipeline as tp

    tp.load_bars = loader  # handler 的函数级 import 在请求期解析到此替身
    app = create_app()
    app.dependency_overrides[get_decision_workflow_service] = lambda: svc
    db_file = tmp_path / ("klines.db" if db else "missing.db")
    if db:
        db_file.touch()
    monkeypatch.setenv("SELENE_KLINES_DB", str(db_file))
    return app


@pytest.fixture(autouse=True)
def _restore_tp_load_bars():
    """手动属性替换后恢复模块原状（monkeypatch 无法包住 _make_app 内的替换）。"""
    import selene_space_decision_workflow.trading_pipeline as tp

    original = tp.load_bars
    yield
    tp.load_bars = original


@pytest.fixture(autouse=True)
def _no_experiences(monkeypatch):
    """隔离 build_full_input_data → project_experiences 的文件系统读取。"""
    import selene_space_decision_workflow.trading_pipeline as tp

    monkeypatch.setattr(tp, "load_experiences", lambda *a, **k: [])


_PAYLOAD = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "bars": 120,
    "market_type": "futures",
}


@pytest.mark.asyncio
async def test_htf_interval_aggregates_summary_and_passes_to_service(
    tmp_path, monkeypatch
):
    htf_bars = _bars(60, interval_seconds=4 * 3600)
    loader = _FakeLoader({"1h": _bars(120), "4h": htf_bars})
    svc = _FakeDWService()
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/analyze-trading",
            json={**_PAYLOAD, "htf_interval": "4h"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["record_id"] == "dw-htf-test"
    assert body["input_data"]["interval"] == "1h"

    assert len(svc.calls) == 1
    call = svc.calls[0]
    # 与真 htf_summary 纯函数逐键对账（端点只做取数 + 接线）
    expected = real_htf_summary(htf_bars, "BTC/USDT", "4h")
    assert call["htf_summary_data"] == expected
    assert call["htf_summary_data"] is not None
    assert {
        "symbol",
        "interval",
        "bars_total",
        "window_start_iso",
        "window_end_iso",
        "last_close",
        "atr14",
        "range_high_20",
        "range_low_20",
    } <= set(call["htf_summary_data"])
    assert call["htf_summary_data"]["interval"] == "4h"
    assert call["htf_summary_data"]["bars_total"] == 60

    # 取数契约：主窗口走默认 min_bars=50；HTF 走 min_bars=0（薄序列降级不中止）
    assert [c["interval"] for c in loader.calls] == ["1h", "4h"]
    assert loader.calls[0]["min_bars"] == 50
    assert loader.calls[1]["min_bars"] == 0
    assert loader.calls[1]["market_type"] == "futures"


@pytest.mark.asyncio
async def test_htf_thin_series_degrades_to_none(tmp_path, monkeypatch):
    loader = _FakeLoader({"1h": _bars(120), "4h": _bars(10)})
    svc = _FakeDWService()
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/analyze-trading",
            json={**_PAYLOAD, "htf_interval": "4h"},
        )
    # <50 根：降级省略,不报错
    assert resp.status_code == 200
    assert len(svc.calls) == 1
    assert svc.calls[0]["htf_summary_data"] is None
    assert loader.calls[1]["min_bars"] == 0


@pytest.mark.asyncio
async def test_htf_interval_none_keeps_current_behavior(tmp_path, monkeypatch):
    loader = _FakeLoader({"1h": _bars(120), "4h": _bars(60)})
    svc = _FakeDWService()
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/analyze-trading", json=_PAYLOAD
        )
    assert resp.status_code == 200
    assert len(svc.calls) == 1
    assert svc.calls[0]["htf_summary_data"] is None
    # 未给 htf_interval → 只拉主窗口,无第二次取数
    assert [c["interval"] for c in loader.calls] == ["1h"]


@pytest.mark.asyncio
async def test_klines_db_gate_still_applies_with_htf_interval(tmp_path, monkeypatch):
    loader = _FakeLoader({"1h": _bars(120)})
    svc = _FakeDWService()
    app = _make_app(svc, loader, tmp_path, monkeypatch, db=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/analyze-trading",
            json={**_PAYLOAD, "htf_interval": "4h"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "klines_db_unavailable"
    assert svc.calls == []
