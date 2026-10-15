"""POST /analyze-trading ``incremental=true`` — the HTTP counterpart of the
CLI's ``--incremental`` branch.

That branch does two things together: ``incremental_input`` builds the
K-continued window from the prior record + the bars arrived since, and
``evaluate_continuity`` applies the deterministic P2-001 guard over those
new bars. Wiring only the first half would drop the guard silently — no
error, tests green, wrong diagnosis — so the guard call is pinned here.

Prior resolution is its own thing: the newest same-symbol/same-interval
record that has a Stage1 result, *regardless of outcome* (unlike the
outcome-review predicate in pa_outcome._wants_review).

Hermetic: ``load_bars`` is monkeypatched (function-level import resolves at
request time), the klines-db gate points at a temp file, and the record
repo is a fake — no storage, no LLM.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.core.dependencies import get_decision_workflow_service
from app.core.trading_input import resolve_prior_record
from app.main import create_app
from httpx import ASGITransport, AsyncClient

_START_TS = 1_700_000_000
_STEP = 3600
_PAYLOAD = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "bars": 120,
    "market_type": "futures",
    "incremental": True,
}


def _bars(n: int) -> list[dict[str, Any]]:
    """Deterministic synthetic bars, oldest-first, ts ascending (seconds)."""
    base, step = 100.0, 0.5
    return [
        {
            "ts": _START_TS + i * _STEP,
            "open": base + i * step,
            "high": base + i * step + 1.0,
            "low": base + i * step - 1.0,
            "close": base + i * step + 0.25,
            "volume": 10.0 + i,
        }
        for i in range(n)
    ]


def _prior_record(
    *,
    interval: str = "1h",
    window_end_ts: int = _START_TS + 119 * _STEP,
    bars_total: int = 120,
    stage2: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
    with_stage1: bool = True,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "meta": {"input_id": "prior-1"},
        "input_data": {
            "symbol": "BTC/USDT",
            "interval": interval,
            "summary": {
                "symbol": "BTC/USDT",
                "interval": interval,
                "bars_total": bars_total,
                "window_end_iso": window_end_ts,
            },
        },
        "stage1": (
            {"diagnosis_or_decision": {"state": "trend_up", "gate_result": "proceed"}}
            if with_stage1
            else {}
        ),
    }
    if stage2 is not None:
        record["stage2"] = {"diagnosis_or_decision": stage2}
    if outcome is not None:
        record["outcome"] = outcome
    return record


class _FakeLoader:
    """trading_pipeline.load_bars 替身:按 interval 分发并记录调用。"""

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
            {"symbol": symbol, "interval": interval, "min_bars": min_bars}
        )
        rows = self.series.get(interval, [])
        if len(rows) < min_bars:
            raise RuntimeError(f"insufficient bars in {db_path}: {len(rows)}")
        return list(rows)


class _FakeRecordRepo:
    """analysis_records repo 替身:只实现端点用到的那一面。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[dict[str, Any]] = []

    async def list_records(
        self, symbol: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append({"symbol": symbol, "limit": limit})
        return list(self.records)


class _StubDump:
    """model_dump() 鸭子类型替身(handler 只调 model_dump)。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return self._data


class _FakeDWService:
    """DecisionWorkflowService 替身:捕获 input_data + 暴露 record repo。"""

    def __init__(self, repo: _FakeRecordRepo | None = None) -> None:
        self.repo = repo
        self.calls: list[dict[str, Any]] = []

    async def _get_record_repo(self) -> _FakeRecordRepo | None:
        return self.repo

    async def analyze_trading(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        trading_stance: str | None = None,
        htf_summary_data: dict[str, Any] | None = None,
        demo: bool = False,
    ) -> Any:
        self.calls.append(
            {
                "input_data": input_data,
                "input_id": input_id,
                "htf_summary_data": htf_summary_data,
                "demo": demo,
            }
        )
        return SimpleNamespace(
            meta=_StubDump({"input_id": input_id, "record_id": "dw-incr-test"}),
            input_data=input_data,
            stage1=_StubDump({"decision_summary": {"bias": "neutral"}}),
            stage2=_StubDump({"decision": "wait"}),
            strategy_files_used=[],
            usage_total={"prompt_tokens": 1, "completion_tokens": 1},
            outcome=None,
            backtest_verification=None,
            followups=None,
        )


def _make_app(
    svc: _FakeDWService,
    loader: _FakeLoader,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    import selene_space_decision_workflow.trading_pipeline as tp

    tp.load_bars = loader  # handler 的函数级 import 在请求期解析到此替身
    app = create_app()
    app.dependency_overrides[get_decision_workflow_service] = lambda: svc
    db_file = tmp_path / "klines.db"
    db_file.touch()
    monkeypatch.setenv("SELENE_KLINES_DB", str(db_file))
    return app


@pytest.fixture(autouse=True)
def _restore_tp_load_bars():
    """手动属性替换后恢复模块原状(monkeypatch 包不住 _make_app 内的替换)。"""
    import selene_space_decision_workflow.trading_pipeline as tp

    original = tp.load_bars
    yield
    tp.load_bars = original


@pytest.fixture(autouse=True)
def _no_experiences(monkeypatch):
    """隔离 enrich_input_data → project_experiences 的文件系统读取。"""
    import selene_space_decision_workflow.trading_pipeline as tp

    monkeypatch.setattr(tp, "load_experiences", lambda *a, **k: [])


async def _post(app: Any, payload: dict[str, Any]) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/v1/decision-workflow/analyze-trading", json=payload
        )


# ---------------------------------------------------------------------------
# resolve_prior_record — D3 predicate (pure, no db)
# ---------------------------------------------------------------------------


def test_resolve_prior_record_matches_interval_and_ignores_outcome():
    """先验谓词 ≠ 复盘谓词:已有 outcome 的记录仍是有效先验。"""
    judged = _prior_record(outcome={"verdict": "win"})
    assert resolve_prior_record([judged], "BTC/USDT", "1h") is judged


def test_resolve_prior_record_skips_unusable_and_takes_newest_first():
    wrong_interval = _prior_record(interval="4h")
    no_stage1 = _prior_record(with_stage1=False)
    usable = _prior_record(bars_total=200)
    older = _prior_record(bars_total=100)
    picked = resolve_prior_record(
        [wrong_interval, no_stage1, usable, older], "BTC/USDT", "1h"
    )
    assert picked is usable

    wrong_symbol = {
        "input_data": {"summary": {"symbol": "ETH/USDT", "interval": "1h"}},
        "stage1": {"diagnosis_or_decision": {"state": "range"}},
    }
    assert resolve_prior_record([wrong_symbol], "BTC/USDT", "1h") is None
    assert resolve_prior_record([], "BTC/USDT", "1h") is None


# ---------------------------------------------------------------------------
# Endpoint degradation paths — 422, never 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_without_prior_returns_422(tmp_path, monkeypatch):
    svc = _FakeDWService(repo=_FakeRecordRepo([]))
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "no_incremental_prior"
    assert svc.calls == []
    # 无 prior 时连 bar 都不该拉 —— 降级发生在取数之前
    assert loader.calls == []


@pytest.mark.asyncio
async def test_incremental_without_record_repo_returns_422(tmp_path, monkeypatch):
    """repo 未接线(无 db)降级为 422,不是 500。"""
    svc = _FakeDWService(repo=None)
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "no_incremental_prior"


@pytest.mark.asyncio
async def test_incremental_without_new_bars_returns_422(tmp_path, monkeypatch):
    """prior 之后没有新 bar → no_new_bars(不是 insufficient_bars / 500)。"""
    repo = _FakeRecordRepo([_prior_record()])
    svc = _FakeDWService(repo=repo)
    loader = _FakeLoader({"1h": _bars(120)})  # 末根 ts == prior 的 window_end
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "no_new_bars"
    assert svc.calls == []


@pytest.mark.asyncio
async def test_incremental_repo_listed_by_symbol_once(tmp_path, monkeypatch):
    repo = _FakeRecordRepo([_prior_record()])
    svc = _FakeDWService(repo=repo)
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 200
    assert repo.calls == [{"symbol": "BTC/USDT", "limit": 50}]


# ---------------------------------------------------------------------------
# Happy path — K-numbering continuity + guard wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_happy_path_continues_k_numbering(tmp_path, monkeypatch):
    repo = _FakeRecordRepo([_prior_record(bars_total=120)])
    svc = _FakeDWService(repo=repo)
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 200

    assert len(svc.calls) == 1
    passed = svc.calls[0]["input_data"]
    summary = passed["summary"]
    assert summary["mode"] == "incremental"
    assert summary["prior_bars_total"] == 120
    assert summary["new_bars_count"] == 5
    # K 编号续接:先验窗口止于 K120 → 新 bar 从 K121 起
    assert [b["k"] for b in summary["bars"]] == [121, 122, 123, 124, 125]
    assert passed["prior_window"] == {
        "end_iso": _START_TS + 119 * _STEP,
        "bars_total": 120,
    }
    # 共享材料块照旧装配(四窗口 / overlay / experiences)
    assert "windows" in passed
    # 服务签名未变(D1):增量只换组装器
    assert svc.calls[0]["htf_summary_data"] is None


@pytest.mark.asyncio
async def test_incremental_applies_continuity_guard(tmp_path, monkeypatch):
    """AC5 回归锁:守卫在增量路上被调用,且结果进 input_data。"""
    import selene_space_decision_workflow.continuity as continuity_mod

    seen: list[tuple[Any, Any]] = []
    real = continuity_mod.evaluate_continuity

    def _spy(last_decision: Any, new_bars: Any):
        seen.append((last_decision, new_bars))
        return real(last_decision, new_bars)

    monkeypatch.setattr(continuity_mod, "evaluate_continuity", _spy)

    # 挂单已挂 9 根 > 3 根过期窗 → limit_order_expiry 否决
    last_decision = {"order": {"type": "limit", "bars_resting": 9}}
    repo = _FakeRecordRepo([_prior_record(stage2=last_decision)])
    svc = _FakeDWService(repo=repo)
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 200

    assert len(seen) == 1
    guard_in, bars_in = seen[0]
    assert guard_in == last_decision
    assert [b["ts"] for b in bars_in] == [
        _START_TS + i * _STEP for i in range(120, 125)
    ]

    guard_out = svc.calls[0]["input_data"]["continuity_guard"]
    assert guard_out["allowed"] is False
    assert guard_out["rule"] == "limit_order_expiry"


@pytest.mark.asyncio
async def test_incremental_without_stage2_skips_guard(tmp_path, monkeypatch):
    """无 Stage2 决策 ⇒ last_decision None ⇒ 不调守卫(CLI 同语义)。"""
    import selene_space_decision_workflow.continuity as continuity_mod

    calls: list[Any] = []
    monkeypatch.setattr(
        continuity_mod, "evaluate_continuity", lambda *a, **k: calls.append(a)
    )
    repo = _FakeRecordRepo([_prior_record()])
    svc = _FakeDWService(repo=repo)
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    resp = await _post(app, _PAYLOAD)
    assert resp.status_code == 200
    assert calls == []
    assert "continuity_guard" not in svc.calls[0]["input_data"]


# ---------------------------------------------------------------------------
# AC1 regression — the default path stays untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_incremental_request_never_touches_prior_repo(tmp_path, monkeypatch):
    repo = _FakeRecordRepo([_prior_record()])
    svc = _FakeDWService(repo=repo)
    loader = _FakeLoader({"1h": _bars(125)})
    app = _make_app(svc, loader, tmp_path, monkeypatch)
    payload = {k: v for k, v in _PAYLOAD.items() if k != "incremental"}
    resp = await _post(app, payload)
    assert resp.status_code == 200
    assert repo.calls == []
    summary = svc.calls[0]["input_data"]["summary"]
    assert summary.get("mode") is None
    assert summary["bars_total"] == 125
