"""US-2A: anchored follow-ups — POST /records/{id}/followup.

A follow-up is anchored to one persisted record: the question is answered
against that record's frozen snapshot (never against live market data) and the
Q&A pair is appended to the same record so the audit trail stays with the
decision it interrogates.

Two contracts are load-bearing here:

* B1 — the trail must EXTEND, not replace. ``append_payload`` has set
  semantics, so reusing it would leave exactly one follow-up on the record no
  matter how many times the user asked. The gate test is "second follow-up →
  len == 2".
* The answer is persisted, not just returned: a fresh service instance (empty
  memory, same db) must read the trail back.

W3 hybrid extension: when the endpoint can read a fresh bar for the record's
symbol, the prompt gains a labeled 【现在读数 · RECONSTRUCTED】 section and the
persisted entry carries ``used_live: true`` — the trail shows which answers
saw data beyond the frozen record. Every gap (missing db, missing keys,
unknown interval) degrades to the fully-anchored Phase-D form.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from app.core.decision_workflow_service import DecisionWorkflowService
from app.core.dependencies import get_decision_workflow_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_decision_workflow.llm_provider import LLMMessage, LLMResponse
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

_CANNED_INPUT: dict[str, Any] = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "summary": {"atr14": 5.0},
}

_FOLLOWUP_PATH = "/api/v1/decision-workflow/records/{rid}/followup"


# ---------------------------------------------------------------------------
# Stub adapter: records the prompts it was handed, one LLM call per follow-up
# ---------------------------------------------------------------------------


class _RecordingAdapter:
    """Stands in for the cached trading adapter.

    Assigned directly to ``svc._trading_adapter`` so ``_get_trading_adapter``
    short-circuits before it ever reaches for settings — no LLM, no config.
    """

    last_usage: dict[str, int] = {"prompt_tokens": 21, "completion_tokens": 8}

    def __init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        self.calls.append(list(messages))
        return LLMResponse(
            content=f"答:{len(self.calls)}",
            reasoning_content=None,
            usage={"prompt_tokens": 21, "completion_tokens": 8},
        )


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSQLiteManager]:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _seed(
    db: AsyncSQLiteManager,
    input_id: str,
    input_data: dict[str, Any] | None = None,
) -> tuple[DecisionWorkflowService, _RecordingAdapter]:
    """A db-backed service holding one persisted record + a stub adapter."""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    seed = DecisionWorkflowService()  # in-memory; only shape is reused
    record = seed.analyze(
        "trading_decision",
        input_data if input_data is not None else _CANNED_INPUT,
        input_id=input_id,
        session_id="s1",
    )
    await svc._persist_record(record)
    adapter = _RecordingAdapter()
    svc._trading_adapter = adapter
    return svc, adapter


def _app(svc: DecisionWorkflowService):
    app = create_app()
    app.dependency_overrides[get_decision_workflow_service] = lambda: svc
    return app


async def _post(svc: DecisionWorkflowService, input_id: str, question: str) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=_app(svc)), base_url="http://test"
    ) as client:
        return await client.post(
            _FOLLOWUP_PATH.format(rid=input_id), json={"question": question}
        )


# ---------------------------------------------------------------------------
# Happy path: answered once, anchored to the record, persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_answers_once_and_returns_trail(
    db: AsyncSQLiteManager,
) -> None:
    svc, adapter = await _seed(db, "fp-happy")

    resp = await _post(svc, "fp-happy", "为什么这里不加仓?")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_id"] == "fp-happy"
    assert len(body["followups"]) == 1
    entry = body["followups"][0]
    assert entry["question"] == "为什么这里不加仓?"
    assert entry["answer"] == "答:1"
    assert entry["usage"] == {"prompt_tokens": 21, "completion_tokens": 8}
    assert entry["at"] > 0  # epoch 秒(与 heartbeat/experience 同款口径)

    # 一次追问 = 一次 LLM(不是每阶段一次,也不是重跑整条管线)
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_followup_prompt_carries_record_snapshot(
    db: AsyncSQLiteManager,
) -> None:
    """上下文来自该记录的冻结快照:带记录锚点 + 问题,不带实时行情。"""
    svc, adapter = await _seed(db, "fp-context")

    await _post(svc, "fp-context", "止损为什么放这么近?")

    assert len(adapter.calls) == 1
    prompt = adapter.calls[0][0].content
    assert "fp-context" in prompt  # 记录锚点
    assert "止损为什么放这么近?" in prompt
    assert "BTC/USDT" in prompt  # 快照里的品种,不是现查的


@pytest.mark.asyncio
async def test_followup_context_includes_outcome_when_judged(
    db: AsyncSQLiteManager,
) -> None:
    """已判定的记录:事后结果进上下文(win/loss 可被追问「为什么亏了」)。"""
    svc, adapter = await _seed(db, "fp-outcome")
    repo = await svc._get_record_repo()
    assert repo is not None
    # 就地改写那一行 —— 再 _persist_record 会插入第二行,而 get_by_input_id 在
    # 同秒两行间按随机 uuid 兜底排序,读回的可能是不带 outcome 的旧行。
    assert await repo.append_payload(
        "fp-outcome", "outcome", {"status": "loss", "pnl_r": -1.0}
    )
    svc._records.pop("fp-outcome", None)  # 让下次读回走 db,拿到带 outcome 的版本

    await _post(svc, "fp-outcome", "这笔为什么止损了?")

    prompt = adapter.calls[0][0].content
    assert "loss" in prompt
    assert "-1.0" in prompt
    # 缺失字段不该以字面 null 出现在给模型的提示里
    assert "null" not in prompt


# ---------------------------------------------------------------------------
# B1 gate: the trail extends, it does not replace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_followup_extends_trail_to_two(
    db: AsyncSQLiteManager,
) -> None:
    """【B1 守门】连续第二条追问后 len(followups) == 2 —— 不是 1。

    这条断言就是 append_payload(set 语义)与 append_followup 的分水岭:
    用错方法时第二个问题会把第一个替换掉,这里必然读到 1。
    """
    svc, adapter = await _seed(db, "fp-b1")

    first = await _post(svc, "fp-b1", "第一个问题")
    second = await _post(svc, "fp-b1", "第二个问题")

    assert len(first.json()["followups"]) == 1
    trail = second.json()["followups"]
    assert len(trail) == 2
    assert [f["question"] for f in trail] == ["第一个问题", "第二个问题"]
    assert [f["answer"] for f in trail] == ["答:1", "答:2"]
    assert len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_second_followup_prompt_sees_first_qa(
    db: AsyncSQLiteManager,
) -> None:
    """第二轮上下文带上此前问答 —— 追问链要能顺着上一轮往下问。"""
    svc, adapter = await _seed(db, "fp-chain")

    await _post(svc, "fp-chain", "第一个问题")
    await _post(svc, "fp-chain", "第二个问题")

    second_prompt = adapter.calls[1][0].content
    assert "第一个问题" in second_prompt
    assert "答:1" in second_prompt


# ---------------------------------------------------------------------------
# Persistence read-back (the answer must survive a restart, not just the call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_trail_reads_back_after_restart(
    db: AsyncSQLiteManager,
) -> None:
    svc, _ = await _seed(db, "fp-restart")
    await _post(svc, "fp-restart", "重启也还在吗?")

    # "重启":新实例,空内存,同一个 db
    restarted = DecisionWorkflowService(db_loader=lambda: db)
    got = await restarted.get_record("fp-restart")

    assert got is not None
    assert got.followups is not None
    assert len(got.followups) == 1
    assert got.followups[0]["question"] == "重启也还在吗?"

    # GET /records/{id} 也走同一投影 —— 前端刷新后能看到追问记录
    async with AsyncClient(
        transport=ASGITransport(app=_app(restarted)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/decision-workflow/records/fp-restart")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["followups"]) == 1


# ---------------------------------------------------------------------------
# Degraded paths: unknown record, empty question, unwired storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_unknown_record_404_without_llm_call(
    db: AsyncSQLiteManager,
) -> None:
    """没这条记录 → 404,且一次 LLM 都不烧(record 未命中在调用之前)。"""
    svc, adapter = await _seed(db, "fp-known")

    resp = await _post(svc, "fp-unknown", "在吗?")

    assert resp.status_code == 404
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_followup_blank_question_422_without_llm_call(
    db: AsyncSQLiteManager,
) -> None:
    svc, adapter = await _seed(db, "fp-blank")

    resp = await _post(svc, "fp-blank", "   ")

    assert resp.status_code == 422
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_followup_without_storage_is_503_not_404() -> None:
    """内存态服务:记录在缓存里,但问答落不了库 → 503,不是 404。

    报 404("没有这条记录")是撒谎 —— 记录就在眼前,是存储没接上。
    """
    svc = DecisionWorkflowService()  # 无 db_loader
    svc.analyze(
        "trading_decision", _CANNED_INPUT, input_id="fp-nostore", session_id="s1"
    )
    adapter = _RecordingAdapter()
    svc._trading_adapter = adapter

    resp = await _post(svc, "fp-nostore", "存得下吗?")

    assert resp.status_code == 503
    assert adapter.calls == []  # 落不了库就不该先烧 token


@pytest.mark.asyncio
async def test_followup_repo_error_502_semantics_absent_in_404(
    db: AsyncSQLiteManager,
) -> None:
    """400 级与 500 级不混淆:未知记录是 404,不是 503/500。"""
    svc, _ = await _seed(db, "fp-codes")

    assert (await _post(svc, "totally-absent", "q")).status_code == 404
    assert (await _post(svc, "fp-codes", "q")).status_code == 200


# ---------------------------------------------------------------------------
# W3 hybrid: fresh "now" reading, labeled RECONSTRUCTED, audited via used_live
# ---------------------------------------------------------------------------


def _make_klines_db(db_path: Path, *, last_bar_ts_ms: int) -> None:
    """Minimal klines.db matching latest_bar_snapshot's query
    (binance/futures, one 1h bar)."""
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
        conn.execute(
            "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "binance",
                "futures",
                "BTC/USDT",
                "1h",
                last_bar_ts_ms,
                100.0,
                101.0,
                99.0,
                100.5,
                1.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _klines_env(db_path: Path):
    """Point SELENE_KLINES_DB at db_path for the block."""
    old = os.environ.get("SELENE_KLINES_DB")
    os.environ["SELENE_KLINES_DB"] = str(db_path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SELENE_KLINES_DB", None)
        else:
            os.environ["SELENE_KLINES_DB"] = old


def _w3_input(window_end: int | str, *, interval: str = "1h") -> dict[str, Any]:
    """Real-pipeline-shaped input: summary.window_end_iso is a ms epoch
    despite the _iso suffix (trading_pipeline writes the raw timestamp).
    The str form feeds the ISO-string tolerance path (older/demo records)."""
    return {
        "symbol": "BTC/USDT",
        "interval": interval,
        "summary": {"window_end_iso": window_end},
    }


_LAST_BAR_MS = 1_700_010_000_000  # window_end + 10h


@pytest.mark.asyncio
async def test_followup_hybrid_injects_now_reading_and_marks_used_live(
    db: AsyncSQLiteManager, tmp_path: Path
) -> None:
    """db 有新鲜 bar → prompt 带【现在读数】段 + bars_since_end 正确 +
    entry.used_live=True;RECORDED 锚定段仍在。"""
    svc, adapter = await _seed(db, "fp-w3-live", _w3_input(1_700_000_000_000))
    kdb = tmp_path / "klines.db"
    _make_klines_db(kdb, last_bar_ts_ms=_LAST_BAR_MS)

    with _klines_env(kdb):
        resp = await _post(svc, "fp-w3-live", "现在还能进场吗?")

    assert resp.status_code == 200, resp.text
    entry = resp.json()["followups"][-1]
    assert entry["used_live"] is True
    assert len(adapter.calls) == 1
    prompt = adapter.calls[0][0].content
    assert "现在读数" in prompt
    assert "RECONSTRUCTED" in prompt
    assert "100.5" in prompt  # last_close from the fresh bar
    # (last_bar_ts - window_end) / interval_ms = 10_000_000 / 3_600_000 → 3
    assert "3 根" in prompt
    # RECORDED anchor section is still there — hybrid, not replacement
    assert "fp-w3-live" in prompt
    assert "BTC/USDT" in prompt


@pytest.mark.asyncio
async def test_followup_hybrid_tolerates_iso_string_window_end(
    db: AsyncSQLiteManager, tmp_path: Path
) -> None:
    """旧/演示记录可能存 ISO 字符串:解析后同样注入,used_live=True。"""
    iso = datetime.fromtimestamp(1_700_006_000_000 / 1000, tz=timezone.utc)
    svc, adapter = await _seed(db, "fp-w3-iso", _w3_input(iso.isoformat()))
    kdb = tmp_path / "klines.db"
    _make_klines_db(kdb, last_bar_ts_ms=_LAST_BAR_MS)

    with _klines_env(kdb):
        resp = await _post(svc, "fp-w3-iso", "现在呢?")

    assert resp.status_code == 200, resp.text
    assert resp.json()["followups"][-1]["used_live"] is True
    assert "现在读数" in adapter.calls[0][0].content


@pytest.mark.asyncio
async def test_followup_without_klines_db_stays_fully_anchored(
    db: AsyncSQLiteManager,
) -> None:
    """db 指向不存在的路径 → 无【现在读数】段,used_live=False,行为与
    Phase D 完全一致。显式指坏路径而非 unset:unset 会落回相对 cwd 的
    data/klines.db,在真仓库根上恰有真库,前提随执行目录漂移。"""
    svc, adapter = await _seed(db, "fp-w3-nodeb", _w3_input(1_700_006_000_000))

    with _klines_env(Path("nonexistent-klines-for-w3-nodeb.db")):
        resp = await _post(svc, "fp-w3-nodeb", "没有行情也能答吗?")

    assert resp.status_code == 200, resp.text
    entry = resp.json()["followups"][-1]
    assert entry["used_live"] is False
    prompt = adapter.calls[0][0].content
    assert "现在读数" not in prompt
    # 全程锚定段仍在
    assert "fp-w3-nodeb" in prompt


@pytest.mark.asyncio
async def test_followup_record_missing_fields_degrades_to_anchored(
    db: AsyncSQLiteManager,
) -> None:
    """记录缺 symbol/interval/window_end(live_read 无从谈起)→ 降级为
    完全锚定,不抛 500。"""
    svc, adapter = await _seed(db, "fp-w3-sparse", {"summary": {"atr14": 5.0}})

    # 甚至连 db 都指好了——降级靠的是字段缺口,不是 db 缺失
    kdb = Path("nonexistent-klines-for-w3.db")
    with _klines_env(kdb):
        resp = await _post(svc, "fp-w3-sparse", "缺字段呢?")

    assert resp.status_code == 200, resp.text
    assert resp.json()["followups"][-1]["used_live"] is False
    assert "现在读数" not in adapter.calls[0][0].content


# ---------------------------------------------------------------------------
# Mission 侧读取器:问答镜像到拥有该记录的 mission
# ---------------------------------------------------------------------------


def _spy_sync(
    monkeypatch: pytest.MonkeyPatch, result: str | None = "mis-1"
) -> list[dict[str, Any]]:
    """Patch the lazily-imported connector and record its kwargs.

    The endpoint imports ``sync_followup_to_mission`` inside the call, so
    patching the module attribute is enough — no reload, no extra wiring.
    """
    calls: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        return result

    monkeypatch.setattr("app.core.pa_mission_sensor.sync_followup_to_mission", _fake)
    return calls


@pytest.mark.asyncio
async def test_followup_projects_to_mission_with_record_symbol(
    db: AsyncSQLiteManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """镜像到 Mission 时带记录品种与原文;无实况读数 ⇒ used_live=False。

    显式指坏 klines 路径:unset 会落回 cwd 相对的 data/klines.db,在真仓库根
    恰有真库,断言随执行目录漂移(同 test_followup_without_klines_db)。
    """
    calls = _spy_sync(monkeypatch)
    svc, _ = await _seed(db, "fp-mission", _w3_input(1_700_000_000_000))

    with _klines_env(Path("nonexistent-klines-for-mission.db")):
        resp = await _post(svc, "fp-mission", "这里为什么减仓?")

    assert resp.status_code == 200, resp.text
    assert calls == [
        {
            "symbol": "BTC/USDT",
            "record_id": "fp-mission",
            "question": "这里为什么减仓?",
            "used_live": False,
        }
    ]


@pytest.mark.asyncio
async def test_followup_projection_reports_used_live(
    db: AsyncSQLiteManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """实况读数注入成功时,镜像条目 used_live=True —— 时间线能看到哪条答过现价。"""
    calls = _spy_sync(monkeypatch)
    svc, _ = await _seed(db, "fp-mission-live", _w3_input(1_700_000_000_000))
    kdb = tmp_path / "klines.db"
    _make_klines_db(kdb, last_bar_ts_ms=_LAST_BAR_MS)

    with _klines_env(kdb):
        resp = await _post(svc, "fp-mission-live", "现在还能进吗?")

    assert resp.status_code == 200, resp.text
    assert calls[0]["used_live"] is True
    assert calls[0]["record_id"] == "fp-mission-live"


@pytest.mark.asyncio
async def test_followup_survives_mission_projection_failure(
    db: AsyncSQLiteManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """【best-effort 守门】Mission 侧炸了也必须 200 且问答已落库。

    问答此刻已经写在记录上,Mission 时间线只是第二个读取器 —— 它挂了不该
    连累主路径,否则会在用户眼里变成"追问失败"。
    """
    calls = _spy_sync(monkeypatch)
    svc, adapter = await _seed(db, "fp-mission-boom")

    async def _boom(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        raise RuntimeError("mission repo down")

    monkeypatch.setattr("app.core.pa_mission_sensor.sync_followup_to_mission", _boom)

    with _klines_env(Path("nonexistent-klines-for-boom.db")):
        resp = await _post(svc, "fp-mission-boom", "投影挂了也要答")

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1  # 真试过,不是静默跳过
    trail = resp.json()["followups"]
    assert len(trail) == 1
    assert trail[0]["question"] == "投影挂了也要答"
    # 落库也没被连累:新实例读得回
    restarted = DecisionWorkflowService(db_loader=lambda: db)
    got = await restarted.get_record("fp-mission-boom")
    assert got is not None and got.followups and len(got.followups) == 1


@pytest.mark.asyncio
async def test_followup_unknown_record_skips_projection(
    db: AsyncSQLiteManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 路径不该碰 Mission:没有记录就没有可镜像的问答。"""
    calls = _spy_sync(monkeypatch)
    svc, _ = await _seed(db, "fp-mission-known")

    resp = await _post(svc, "fp-mission-absent", "在吗?")

    assert resp.status_code == 404
    assert calls == []
