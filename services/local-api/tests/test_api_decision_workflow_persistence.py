"""A1 persistence tests for the decision-workflow trading pipeline.

The service's read-side cache (``self._records``) is process-local, so before
A1 a completed two-stage run vanished on restart.  A1 lands real-LLM records
in the shared v009 ``analysis_records`` table; ``get_record`` falls back to
that table (looked up by ``input_id``) when memory misses.

Contract pinned here: the canned ``analyze()`` path — and therefore the S6
demo route, which returns before the persist point — must NOT write to the
audit table.  Persisting synthesized answers would corrupt the audit trail.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from app.core.decision_workflow_service import DecisionWorkflowService
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_CANNED_INPUT: dict[str, Any] = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "summary": {"atr14": 5.0, "interval": "1h"},
}


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSQLiteManager]:
    """Real SQLite (in-memory) with every migration played, v009 included."""
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


@pytest.fixture
def demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the S6 demo gate (per-test, auto-restored)."""
    monkeypatch.setenv("SELENE_PA_DEMO_MODE", "1")


def _canned_record(svc: DecisionWorkflowService, input_id: str) -> Any:
    return svc.analyze(
        "trading_decision",
        _CANNED_INPUT,
        input_id=input_id,
        session_id="s1",
    )


# ---------------------------------------------------------------------------
# Persist + read-back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_record_lands_in_v009(db: AsyncSQLiteManager) -> None:
    """A record handed to the persist path is queryable by its input_id."""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    record = _canned_record(svc, "a1-land")

    await svc._persist_record(record)

    repo = await svc._get_record_repo()
    assert repo is not None
    payload = await repo.get_by_input_id("a1-land")
    assert payload is not None
    assert payload["meta"]["input_id"] == "a1-land"
    assert payload["input_data"]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_get_record_falls_back_to_db_after_restart(
    db: AsyncSQLiteManager,
) -> None:
    """A fresh service instance (empty memory) reads the record back from db."""
    first = DecisionWorkflowService(db_loader=lambda: db)
    await first._persist_record(_canned_record(first, "a1-restart"))

    # "Restart": new instance, same db → memory is empty, db fallback serves it.
    restarted = DecisionWorkflowService(db_loader=lambda: db)
    got = await restarted.get_record("a1-restart")

    assert got is not None
    assert got.meta.input_id == "a1-restart"
    assert got.stage1 is not None


@pytest.mark.asyncio
async def test_get_record_reloads_from_db_when_memory_misses(
    db: AsyncSQLiteManager,
) -> None:
    """Repeated lookups stay consistent (db hit is cached on first read)."""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    await svc._persist_record(_canned_record(svc, "a1-twice"))

    other = DecisionWorkflowService(db_loader=lambda: db)
    first_read = await other.get_record("a1-twice")
    second_read = await other.get_record("a1-twice")

    assert first_read is not None
    assert second_read is not None
    assert first_read.meta.input_id == second_read.meta.input_id


# ---------------------------------------------------------------------------
# Miss / degraded paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_record_returns_none_when_absent(db: AsyncSQLiteManager) -> None:
    svc = DecisionWorkflowService(db_loader=lambda: db)
    assert await svc.get_record("no-such-id") is None


@pytest.mark.asyncio
async def test_no_db_loader_stays_memory_only() -> None:
    """Without a db_loader the service keeps pre-A1 semantics exactly."""
    svc = DecisionWorkflowService()
    record = _canned_record(svc, "mem-only")

    await svc._persist_record(record)  # best-effort no-op, must not raise

    assert await svc.get_record("mem-only") is record
    assert await svc.get_record("not-there") is None


@pytest.mark.asyncio
async def test_persist_failure_does_not_raise(
    db: AsyncSQLiteManager,
) -> None:
    """A storage error is swallowed (analysis response must still succeed)."""

    class _Boom:
        async def save(self, _record: dict[str, Any]) -> str:
            raise RuntimeError("disk full")

    svc = DecisionWorkflowService(db_loader=lambda: db)
    svc._record_repo = _Boom()

    await svc._persist_record(_canned_record(svc, "a1-boom"))  # no raise


# ---------------------------------------------------------------------------
# Real-LLM branch wiring: analyze_trading actually calls the persist path
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Minimal adapter stand-in so the real branch runs without an LLM."""

    last_usage: dict[str, int] = {"prompt_tokens": 11, "completion_tokens": 7}


class _FakeWorkflow:
    def __init__(self, record: Any) -> None:
        self._record = record

    def analyze(self, _input_data: Any, **kw: Any) -> Any:
        return self._record


class _FakeTpMod:
    """Stands in for selene_space_decision_workflow.trading_pipeline."""

    def __init__(self, record: Any) -> None:
        self._record = record

    def build_trading_workflow(self, **_kw: Any) -> tuple[Any, Any]:
        return _FakeWorkflow(self._record), None


@pytest.mark.asyncio
async def test_analyze_trading_real_branch_persists(
    db: AsyncSQLiteManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real-LLM branch must persist — driven with a stubbed workflow."""
    seed = DecisionWorkflowService()  # no db needed to mint a record shape
    seed_record = _canned_record(seed, "a1-real-branch")

    svc = DecisionWorkflowService(db_loader=lambda: db)

    async def _adapter() -> Any:
        return _FakeAdapter()

    monkeypatch.setattr(svc, "_get_trading_adapter", _adapter)
    svc._tp_mod = _FakeTpMod(seed_record)

    record = await svc.analyze_trading(
        _CANNED_INPUT, input_id="a1-real-branch", session_id="s1"
    )

    assert record.meta.input_id == "a1-real-branch"
    repo = await svc._get_record_repo()
    assert repo is not None
    assert await repo.get_by_input_id("a1-real-branch") is not None


# ---------------------------------------------------------------------------
# Zero-pollution: canned paths never reach the audit table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_path_does_not_persist(
    db: AsyncSQLiteManager,
    demo_env: None,
) -> None:
    """S6 demo route returns before the persist point — nothing hits v009."""
    svc = DecisionWorkflowService(db_loader=lambda: db)

    await svc.analyze_trading(_CANNED_INPUT, input_id="demo-nopersist", session_id="s1")

    repo = await svc._get_record_repo()
    assert repo is not None
    assert await repo.get_by_input_id("demo-nopersist") is None


# ---------------------------------------------------------------------------
# W2 US-001: models round-trip for the two audit-chain keys + attach_backtest
# ---------------------------------------------------------------------------


def _record_to_payload(record: Any) -> dict[str, Any]:
    payload: dict[str, Any] = record.model_dump(mode="json")
    return payload


def test_models_two_new_keys_roundtrip() -> None:
    """AnalysisRecord must carry backtest_verification/outcome through
    model_validate — undeclared top-level keys are silently dropped by
    Pydantic v2, so the declaration is the whole point (Architect BLOCK)."""
    from selene_space_decision_workflow.models import AnalysisRecord

    seed = DecisionWorkflowService()
    record = _canned_record(seed, "w2-models")
    record.backtest_verification = {"result": "win", "pnl_r": 1.5}
    record.outcome = {"status": "win", "closed_at": "2026-09-12T10:00:00"}

    payload = _record_to_payload(record)
    revived = AnalysisRecord.model_validate(payload)
    assert revived.backtest_verification == {"result": "win", "pnl_r": 1.5}
    assert revived.outcome == {"status": "win", "closed_at": "2026-09-12T10:00:00"}

    # 缺省分支:老记录没有这两个键 → None,不炸。
    legacy = _record_to_payload(_canned_record(seed, "w2-legacy"))
    revived_legacy = AnalysisRecord.model_validate(legacy)
    assert revived_legacy.backtest_verification is None
    assert revived_legacy.outcome is None


@pytest.mark.asyncio
async def test_attach_backtest_refreshes_cache_and_db(db: AsyncSQLiteManager) -> None:
    """attach_backtest: repo row updated AND the cache-first read path sees it
    immediately (get_record serves the refreshed record, not stale memory)."""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    record = _canned_record(svc, "w2-attach")
    await svc._persist_record(record)
    # Prime the cache the way a normal request flow would.
    assert await svc.get_record("w2-attach") is not None

    ok = await svc.attach_backtest("w2-attach", {"result": "win", "trades": 7})
    assert ok is True

    got = await svc.get_record("w2-attach")  # cache-first — must see the new key
    assert got is not None
    assert got.backtest_verification == {"result": "win", "trades": 7}

    # Cross-restart read: fresh service, same db → still readable (A1 bar).
    restarted = DecisionWorkflowService(db_loader=lambda: db)
    got2 = await restarted.get_record("w2-attach")
    assert got2 is not None
    assert got2.backtest_verification == {"result": "win", "trades": 7}


@pytest.mark.asyncio
async def test_attach_backtest_missing_record_returns_false(
    db: AsyncSQLiteManager,
) -> None:
    """repo 未命中 input_id → attach_backtest False,不炸(Architect 死法③)。"""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    assert await svc.attach_backtest("never-persisted", {"result": "win"}) is False


@pytest.mark.asyncio
async def test_attach_backtest_repo_boom_returns_false(
    db: AsyncSQLiteManager,
) -> None:
    """Storage error inside append_payload → False (caller's request lives)."""

    class _BoomRepo:
        async def append_payload(
            self, _input_id: str, _key: str, _value: dict[str, Any]
        ) -> bool:
            raise RuntimeError("disk full")

    svc = DecisionWorkflowService(db_loader=lambda: db)
    svc._record_repo = _BoomRepo()
    assert await svc.attach_backtest("w2-boom", {"result": "win"}) is False


@pytest.mark.asyncio
async def test_attach_backtest_no_db_loader_returns_false() -> None:
    """No db wired → False (memory-only service cannot attach anything)."""
    svc = DecisionWorkflowService()
    assert await svc.attach_backtest("mem-only", {"result": "win"}) is False


# ---------------------------------------------------------------------------
# list_records (review panel feed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_records_returns_persisted_newest_first(
    db: AsyncSQLiteManager,
) -> None:
    """Persisted records come back newest-first with meta/summary/direction."""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    r1 = _canned_record(svc, "list-old")
    await svc._persist_record(r1)
    r2 = _canned_record(svc, "list-new")
    await svc._persist_record(r2)

    got = await svc.list_records()
    ids = [r.meta.input_id for r in got]
    assert "list-old" in ids and "list-new" in ids


@pytest.mark.asyncio
async def test_list_records_symbol_filter(db: AsyncSQLiteManager) -> None:
    """symbol 过滤只留匹配品种;interval 过滤同走 summary dict。"""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    r = _canned_record(svc, "list-btc")
    await svc._persist_record(r)

    got_btc = await svc.list_records(symbol="BTC/USDT")
    assert [x.meta.input_id for x in got_btc] == ["list-btc"]

    got_none = await svc.list_records(symbol="ETH/USDT")
    assert got_none == []


@pytest.mark.asyncio
async def test_list_records_interval_filter_reads_summary_dict(
    db: AsyncSQLiteManager,
) -> None:
    """interval 过滤从 input_data['summary']['interval'] 直取(dict 非模型)。"""
    svc = DecisionWorkflowService(db_loader=lambda: db)
    r = _canned_record(svc, "list-1h")
    await svc._persist_record(r)

    got = await svc.list_records(interval="1h")
    assert [x.meta.input_id for x in got] == ["list-1h"]
    assert (await svc.list_records(interval="4h")) == []


@pytest.mark.asyncio
async def test_list_records_no_db_returns_empty() -> None:
    """No db wired → empty list (memory-only service),不炸。"""
    svc = DecisionWorkflowService()
    assert await svc.list_records() == []
