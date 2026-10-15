"""S6 demo-mode tests for the decision-workflow trading pipeline.

SELENE_PA_DEMO_MODE gates a canned walk-through route inside
DecisionWorkflowService.analyze_trading: when enabled, the trading analyze
request is answered by the canned ``analyze("trading_decision")`` path (no
real LLM call, no bar data consumed) and the record's meta carries
``demo=True``. When disabled (default) behavior is byte-identical to the
pre-S6 real-LLM path.

The demo record is an honest residual card: stage2.order is None (the
canned synthesizer never fabricates price levels) — asserted here so the
zero-fakery contract is pinned.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from app.core.decision_workflow_service import DecisionWorkflowService
from app.main import create_app
from app.core.dependencies import get_decision_workflow_service
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _service() -> DecisionWorkflowService:
    return DecisionWorkflowService()


def _seed_klines_db(db_path: Path, *, bars: int = 120) -> None:
    """Create a minimal klines.db matching load_bars' query (binance/futures)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE klines (
                exchange TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL
            )
            """
        )
        base_ts = 1_700_000_000_000  # ms
        for i in range(bars):
            conn.execute(
                "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "binance",
                    "futures",
                    "BTC/USDT",
                    "1h",
                    base_ts + i * 3_600_000,
                    100.0 + i * 0.1,
                    101.0 + i * 0.1,
                    99.0 + i * 0.1,
                    100.5 + i * 0.1,
                    10.0,
                ),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the demo gate (per-test, auto-restored)."""
    monkeypatch.setenv("SELENE_PA_DEMO_MODE", "1")


# ---------------------------------------------------------------------------
# Service-level: gate off (default) vs gate on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_off_default_real_path_taken() -> None:
    """默认关: analyze_trading 不走 canned 分支 — 无 LLM 配置时应报
    LLM-not-configured RuntimeError(与现状一致),而非返回 canned record。"""
    svc = _service()
    with pytest.raises(RuntimeError, match="LLM not configured|not configured"):
        await svc.analyze_trading(
            {"symbol": "BTC/USDT", "interval": "1h"},
            input_id="gate-off-1",
            session_id="s1",
        )
    assert await svc.get_record("gate-off-1") is None


@pytest.mark.asyncio
async def test_gate_on_returns_canned_record_with_demo_meta(demo_env: None) -> None:
    """开态: 请求路由到 canned 树,record.meta.demo=True,order 保持 None。"""
    svc = _service()
    record = await svc.analyze_trading(
        {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
        input_id="demo-1",
        session_id="s1",
    )
    assert record.meta.demo is True
    assert record.meta.input_id == "demo-1"
    # canned 形态:Stage1 走树,Stage2 合成 order=None(残卡,零造假)
    assert record.stage2 is not None
    assert record.stage2.diagnosis_or_decision.get("order") is None
    # canned trace 非空(树走查真的发生了)
    assert record.stage1.trace, "canned Stage1 trace must be non-empty"
    # 记录可经 get_record 读回(demo 标记一致)
    fetched = await svc.get_record("demo-1")
    assert fetched is not None
    assert fetched.meta.demo is True


@pytest.mark.asyncio
async def test_gate_on_does_not_touch_real_llm(
    demo_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开态隔离: canned 分支先于任何真实 LLM 装配触发 — mock 掉
    _get_trading_adapter 断言零调用,并证明 trading_pipeline 模块不会被加载。"""
    svc = _service()
    adapter_called = False

    async def _fail() -> Any:
        nonlocal adapter_called
        adapter_called = True
        raise AssertionError("real-LLM adapter must not be built in demo mode")

    monkeypatch.setattr(svc, "_get_trading_adapter", _fail)
    record = await svc.analyze_trading(
        {"symbol": "BTC/USDT", "interval": "1h"},
        input_id="demo-iso-1",
        session_id="s1",
    )
    assert record.meta.demo is True
    assert adapter_called is False
    # trading_pipeline 模块懒加载发生在 canned 分支之后 — 不应被 import
    assert svc._tp_mod is None


@pytest.mark.asyncio
async def test_gate_off_other_trees_unaffected() -> None:
    """关态: canned analyze(其他树) 完全不受门控影响 — 行为与现状一致。"""
    svc = _service()
    record = svc.analyze(
        "credit_approval",
        {"loan_amount": 50000},
        input_id="credit-1",
        session_id="s1",
    )
    assert record.meta.demo is False
    assert record.stage2 is not None


@pytest.mark.asyncio
async def test_gate_on_other_trees_still_canned_without_demo_flag(
    demo_env: None,
) -> None:
    """开态: 门控只影响 analyze_trading 路由 — 直接 analyze() 的 canned 树
    不打 demo 标(它们本来就是演示,但标记语义限定在 trading 走查入口)。"""
    svc = _service()
    record = svc.analyze(
        "credit_approval",
        {"loan_amount": 50000},
        input_id="credit-2",
        session_id="s1",
    )
    assert record.meta.demo is False


# ---------------------------------------------------------------------------
# Endpoint-level: POST /analyze-trading through the FastAPI app
# ---------------------------------------------------------------------------


def _make_app(svc: DecisionWorkflowService):
    app = create_app()
    app.dependency_overrides[get_decision_workflow_service] = lambda: svc
    return app


@pytest.mark.asyncio
async def test_endpoint_gate_on_returns_demo_record(
    demo_env: None, tmp_path: Path
) -> None:
    """端点级: 开态下 /analyze-trading 返回 canned 形态 + meta.demo=True 透传
    到响应 JSON;order 缺失(残卡);零真 LLM。"""
    db = tmp_path / "klines.db"
    _seed_klines_db(db)
    svc = _service()
    app = _make_app(svc)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        import os

        old = os.environ.get("SELENE_KLINES_DB")
        os.environ["SELENE_KLINES_DB"] = str(db)
        try:
            resp = await client.post(
                "/api/v1/decision-workflow/analyze-trading",
                json={"symbol": "BTC/USDT", "interval": "1h", "bars": 120},
            )
        finally:
            if old is None:
                os.environ.pop("SELENE_KLINES_DB", None)
            else:
                os.environ["SELENE_KLINES_DB"] = old
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["demo"] is True
    assert body["stage2"]["diagnosis_or_decision"]["order"] is None
    assert body["stage2"]["diagnosis_or_decision"]["terminal_outcome"] in {
        "trade",
        "wait",
        "reject",
    }
    # 走查痕迹:Stage1 合成 trace 在 StageRecord 顶层(diagnosis_or_decision
    # 只承载 diagnosis_summary={state, terminal});Stage2 链在 stage2 dump 内
    assert body["stage1"]["trace"]
    assert body["stage2"]["diagnosis_or_decision"]["trace"]


@pytest.mark.asyncio
async def test_endpoint_gate_off_real_path_unchanged(tmp_path: Path) -> None:
    """端点级: 关态(默认)行为与现状一致 — 无 LLM 配置返回 503
    llm_not_configured(而非 canned 假应答)。"""
    db = tmp_path / "klines.db"
    _seed_klines_db(db)
    svc = _service()
    app = _make_app(svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        import os

        old = os.environ.get("SELENE_KLINES_DB")
        os.environ["SELENE_KLINES_DB"] = str(db)
        try:
            resp = await client.post(
                "/api/v1/decision-workflow/analyze-trading",
                json={"symbol": "BTC/USDT", "interval": "1h", "bars": 120},
            )
        finally:
            if old is None:
                os.environ.pop("SELENE_KLINES_DB", None)
            else:
                os.environ["SELENE_KLINES_DB"] = old
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "llm_not_configured"


# ---------------------------------------------------------------------------
# Phase D: explicit demo=True — the REAL pipeline, badged, kept out of the db
# ---------------------------------------------------------------------------
#
# These run the real path with a stub adapter and a stub trading_pipeline so
# no LLM and no bars are needed. The stub pipeline returns a genuinely shaped
# AnalysisRecord (built from the canned analyze()) carrying a real order, so
# the assertions below distinguish "real pipeline ran" from "canned
# walk-through" by observable content, not by a mock count alone.


class _StubAdapter:
    """Records every LLM call; stands in for the cached trading adapter."""

    last_usage: dict[str, int] = {"prompt_tokens": 7, "completion_tokens": 3}

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def call(self, messages: Any) -> Any:
        from selene_space_decision_workflow.llm_provider import LLMResponse

        self.calls.append(messages)
        return LLMResponse(
            content="ok",
            reasoning_content=None,
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        )


def _real_shaped_record(input_id: str, session_id: str) -> Any:
    """A canned record mutated to look like a real one: Stage2 carries an order."""
    base = _service().analyze(
        "trading_decision",
        {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
        input_id=input_id,
        session_id=session_id,
    )
    assert base.stage2 is not None
    order = {
        "direction": "long",
        "entry": 101.0,
        "stop_loss": 99.0,
        "take_profit": 105.0,
    }
    stage2 = base.stage2.model_copy(
        update={
            "diagnosis_or_decision": {
                **base.stage2.diagnosis_or_decision,
                "decision": "trade",
                "order": order,
            }
        }
    )
    return base.model_copy(update={"stage2": stage2})


class _StubPipeline:
    """Stands in for selene_space_decision_workflow.trading_pipeline.

    Records whether the shared LLM lock was held when the workflow was built
    (the build happens inside ``analyze_trading``'s lock scope) and what
    kwargs it was handed.
    """

    def __init__(self, adapter: _StubAdapter, svc: Any) -> None:
        self._adapter = adapter
        self._svc = svc
        self.kwargs: dict[str, Any] | None = None
        self.lock_held: bool | None = None
        self.calls = 0

    def build_trading_workflow(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        self.lock_held = self._svc._llm_lock.locked()
        self.calls += 1

        adapter = self._adapter

        class _WF:
            def analyze(
                self, input_data: Any, *, input_id: str, session_id: str
            ) -> Any:
                adapter.call([{"role": "user", "content": "real pipeline"}])
                return _real_shaped_record(input_id, session_id)

        return _WF(), None


def _wire_real_path(
    svc: DecisionWorkflowService, adapter: _StubAdapter
) -> _StubPipeline:
    """Point the service at the stub adapter + stub pipeline (no LLM, no bars)."""
    pipeline = _StubPipeline(adapter, svc)
    svc._trading_adapter = adapter
    svc._tp_mod = pipeline
    return pipeline


@pytest.mark.asyncio
async def test_demo_true_runs_real_pipeline_and_badges() -> None:
    """demo=True → 真管线(非 canned)+ meta.demo=True + 真订单在场。"""
    svc = _service()
    adapter = _StubAdapter()
    pipeline = _wire_real_path(svc, adapter)

    record = await svc.analyze_trading(
        {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
        input_id="demo-real-1",
        session_id="s1",
        demo=True,
    )

    # 真管线:build_trading_workflow 被调用 + 真的调了 LLM(不是 canned 合成)
    assert pipeline.calls == 1
    assert len(adapter.calls) == 1
    assert pipeline.kwargs is not None
    # canned 路径的残留特征是不存在的:这里 Stage2 带真订单,树走查是合成的
    assert record.stage2 is not None
    assert record.stage2.diagnosis_or_decision.get("order") is not None
    # 徽章标记
    assert record.meta.demo is True


@pytest.mark.asyncio
async def test_demo_true_holds_the_shared_llm_lock() -> None:
    """【B2①串行】真 LLM 调用在共享 asyncio.Lock 内 —— 与心跳诊断串行。

    锁被持有的证据取自 pipeline 构建时刻(它在 analyze_trading 的
    ``async with self._llm_lock`` 作用域内执行),而不是事后同任务查询。
    """
    svc = _service()
    adapter = _StubAdapter()
    pipeline = _wire_real_path(svc, adapter)

    await svc.analyze_trading(
        {"symbol": "BTC/USDT", "interval": "1h"},
        input_id="demo-lock-1",
        session_id="s1",
        demo=True,
    )

    assert pipeline.lock_held is True
    # 调用结束后锁已释放(没有泄漏成死锁)
    assert svc._llm_lock.locked() is False


@pytest.mark.asyncio
async def test_demo_true_is_not_persisted_but_real_run_is(tmp_path: Path) -> None:
    """demo=True 不入审计表;同样条件下 demo=False 会入库 —— 对照防真空断言。

    事后结果判定按 symbol 取最新可复盘记录(pa_outcome._wants_review),
    落了库的演示单会被拿去对着真 bar 复盘,当成真决策进经验库。
    """
    from app.core.decision_workflow_service import DecisionWorkflowService as Svc

    db_path = tmp_path / "records.db"
    db = AsyncSQLiteManager(
        str(db_path), migration_runner=MigrationRunner(ALL_MIGRATIONS)
    )
    async with db:
        demo_svc = Svc(db_loader=lambda: db)
        adapter = _StubAdapter()
        _wire_real_path(demo_svc, adapter)
        await demo_svc.analyze_trading(
            {"symbol": "BTC/USDT", "interval": "1h"},
            input_id="demo-nopersist",
            session_id="s1",
            demo=True,
        )
        # "重启":新实例 + 空内存 + 同一个 db
        assert await Svc(db_loader=lambda: db).get_record("demo-nopersist") is None

        real_svc = Svc(db_loader=lambda: db)
        _wire_real_path(real_svc, _StubAdapter())
        await real_svc.analyze_trading(
            {"symbol": "BTC/USDT", "interval": "1h"},
            input_id="real-persist",
            session_id="s1",
        )
        assert await Svc(db_loader=lambda: db).get_record("real-persist") is not None


@pytest.mark.asyncio
async def test_demo_true_overrides_env_gate(demo_env: None) -> None:
    """env 闸门是退化兜底,不能覆盖显式 demo=True 的真管线请求。"""
    svc = _service()
    adapter = _StubAdapter()
    pipeline = _wire_real_path(svc, adapter)

    record = await svc.analyze_trading(
        {"symbol": "BTC/USDT", "interval": "1h"},
        input_id="demo-env-1",
        session_id="s1",
        demo=True,
    )

    assert pipeline.calls == 1  # 真管线,不是 canned 短路
    assert len(adapter.calls) == 1
    assert record.meta.demo is True


@pytest.mark.asyncio
async def test_env_gate_still_canned_when_demo_not_passed(demo_env: None) -> None:
    """向后兼容:不传 demo 时 env 闸门照旧短路到 canned(零真 LLM)。"""
    svc = _service()
    adapter = _StubAdapter()
    pipeline = _wire_real_path(svc, adapter)

    record = await svc.analyze_trading(
        {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
        input_id="demo-env-canned",
        session_id="s1",
    )

    assert pipeline.calls == 0
    assert adapter.calls == []
    assert record.meta.demo is True
    assert record.stage2 is not None
    assert record.stage2.diagnosis_or_decision.get("order") is None


@pytest.mark.asyncio
async def test_endpoint_demo_true_passes_flag_through(tmp_path: Path) -> None:
    """端点级: POST body 里的 demo=true 透传到服务(不再只是 env 开关)。"""
    db = tmp_path / "klines.db"
    _seed_klines_db(db)
    svc = _service()
    adapter = _StubAdapter()
    pipeline = _wire_real_path(svc, adapter)
    app = _make_app(svc)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        import os

        old = os.environ.get("SELENE_KLINES_DB")
        os.environ["SELENE_KLINES_DB"] = str(db)
        try:
            resp = await client.post(
                "/api/v1/decision-workflow/analyze-trading",
                json={
                    "symbol": "BTC/USDT",
                    "interval": "1h",
                    "bars": 120,
                    "demo": True,
                },
            )
        finally:
            if old is None:
                os.environ.pop("SELENE_KLINES_DB", None)
            else:
                os.environ["SELENE_KLINES_DB"] = old

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert pipeline.calls == 1
    assert body["meta"]["demo"] is True
    assert body["stage2"]["diagnosis_or_decision"]["order"] is not None
