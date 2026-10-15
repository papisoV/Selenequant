"""T4 mission sensor connector tests — hermetic (fake repo, no DB).

行为契约三态:新 symbol 首次 → create + settle needs_input;已 open →
merge 诊断 log 去重(不重复 create);载荷携带 v009 行键(input_id)。
repo 自身契约(唯一性/相位合法性表)由 packages/storage/tests/test_research_missions.py 覆盖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol, cast

import pytest
import pytest_asyncio

from app.core.pa_mission_sensor import (
    build_diagnosis,
    sync_diagnosis_to_mission,
    sync_followup_to_mission,
)
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

if TYPE_CHECKING:
    from collections.abc import Awaitable


class _MissionRepo(Protocol):
    """The slice of ResearchMissionRepository the connector touches."""

    def find_open_by_question(
        self, subject_type: str, subject_id: str, question: str
    ) -> Awaitable[dict[str, Any] | None]: ...

    def create(self, **kwargs: Any) -> Awaitable[str]: ...

    def list_missions(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        phase: str | None = None,
        limit: int = 50,
    ) -> Awaitable[list[dict[str, Any]]]: ...

    def transition(
        self,
        mission_id: str,
        to_phase: str,
        *,
        actor: str,
        note: str | None = None,
    ) -> Awaitable[dict[str, Any]]: ...

    def update_context(
        self, mission_id: str, context: dict[str, Any], *, merge: bool = True
    ) -> Awaitable[None]: ...

    def append_activity(
        self,
        mission_id: str,
        *,
        actor: str,
        kind: str,
        detail: dict[str, Any] | None = None,
    ) -> Awaitable[int]: ...


class _FakeRepo:
    def __init__(
        self,
        open_mission: dict[str, Any] | None = None,
        missions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.open_mission = open_mission
        self.missions = missions or []
        self.list_calls: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.transitions: list[tuple[str, str, str]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.activities: list[tuple[str, str, str, dict[str, Any]]] = []

    async def find_open_by_question(
        self, subject_type: str, subject_id: str, question: str
    ) -> dict[str, Any] | None:
        return self.open_mission

    async def create(self, **kwargs: Any) -> str:
        mission_id = f"mis-{len(self.created) + 1:012d}"
        self.created.append(kwargs)
        return mission_id

    async def list_missions(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        phase: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.list_calls.append(
            {"subject_type": subject_type, "subject_id": subject_id, "limit": limit}
        )
        return self.missions

    async def transition(
        self, mission_id: str, to_phase: str, *, actor: str, note: str | None = None
    ) -> dict[str, Any]:
        self.transitions.append((mission_id, to_phase, actor))
        return {"id": mission_id, "phase": to_phase}

    async def update_context(
        self, mission_id: str, context: dict[str, Any], *, merge: bool = True
    ) -> None:
        self.updates.append((mission_id, context))

    async def append_activity(
        self,
        mission_id: str,
        *,
        actor: str,
        kind: str,
        detail: dict[str, Any] | None = None,
    ) -> int:
        self.activities.append((mission_id, actor, kind, detail or {}))
        return len(self.activities)


def _diagnosis() -> dict[str, Any]:
    payload: dict[str, Any] = build_diagnosis(
        input_id="a" * 32,
        interval="1h",
        market_type="futures",
        trading_stance="conservative",
    )
    return payload


def test_build_diagnosis_carries_record_key() -> None:
    payload = _diagnosis()
    assert payload["record_id"] == "a" * 32
    assert payload["interval"] == "1h"
    assert payload["market_type"] == "futures"
    # ISO 时间戳(秒精度),非空
    assert payload["diagnosed_at"]


@pytest.mark.asyncio
async def test_first_diagnosis_creates_and_settles_to_needs_input() -> None:
    repo = _FakeRepo(open_mission=None)
    diag = _diagnosis()
    mission_id = await sync_diagnosis_to_mission(
        symbol="BTC/USDT", diagnosis=diag, repo=cast(_MissionRepo, repo)
    )

    assert mission_id == "mis-000000000001"
    assert len(repo.created) == 1
    created = repo.created[0]
    assert created["subject_type"] == "market"
    assert created["subject_id"] == "BTC/USDT"
    assert created["title"] == "BTC/USDT PA 心跳诊断"
    assert created["context"]["pipeline"] == "pa-heartbeat"
    assert created["context"]["diagnoses"] == [diag]
    # settle: investigation → needs_input,让四动作(approve/redirect/…)立刻可决策
    assert repo.transitions == [("mis-000000000001", "needs_input", "selene")]
    # B2 activity 记录每一次触达,复用 CLI 路径(MissionPersistence)同款 kind
    assert len(repo.activities) == 1
    mission_id, actor, kind, detail = repo.activities[0]
    assert (mission_id, actor, kind) == (
        "mis-000000000001",
        "selene",
        "pa_diagnosis_record",
    )
    assert detail == {
        "input_id": "a" * 32,
        "interval": "1h",
        "diagnosed_at": diag["diagnosed_at"],
    }


@pytest.mark.asyncio
async def test_repeat_diagnosis_merges_instead_of_recreating() -> None:
    prior = build_diagnosis(input_id="b" * 32, interval="1h")
    open_mission: dict[str, Any] = {
        "id": "mis-000000000007",
        "phase": "needs_input",
        "context": {"diagnoses": [prior]},
    }
    repo = _FakeRepo(open_mission=open_mission)
    diag = _diagnosis()
    mission_id = await sync_diagnosis_to_mission(
        symbol="BTC/USDT", diagnosis=diag, repo=cast(_MissionRepo, repo)
    )

    assert mission_id == "mis-000000000007"
    assert repo.created == []  # 唯一性约束:同 subject+question 不重复开 new mission
    assert repo.transitions == []  # 不重复 settle
    assert repo.updates == [
        ("mis-000000000007", {"diagnoses": [prior, diag], "latest_diagnosis": diag})
    ]
    assert repo.activities[-1][2] == "pa_diagnosis_record"


@pytest.mark.asyncio
async def test_resolved_mission_not_open_opens_new() -> None:
    # find_open_by_question 语义:resolved/parked 不算 open → connector 开新 mission
    repo = _FakeRepo(open_mission=None)
    diag = _diagnosis()
    mission_id = await sync_diagnosis_to_mission(
        symbol="SOL/USDT", diagnosis=diag, repo=cast(_MissionRepo, repo)
    )
    assert mission_id == "mis-000000000001"
    assert len(repo.created) == 1


# ---------------------------------------------------------------------------
# 追问 → Mission 投影(锚定追问的 Mission 侧读取器)
# ---------------------------------------------------------------------------


def _mission(mission_id: str, diagnoses: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": mission_id,
        "context": {"diagnoses": diagnoses, "latest_diagnosis": diagnoses[-1]},
    }


@pytest.mark.asyncio
async def test_followup_appends_activity_to_owning_mission() -> None:
    # actor 是 user 不是 selene:心跳诊断是自动的,追问是人问的
    diag = _diagnosis()
    repo = _FakeRepo(missions=[_mission("mis-000000000009", [diag])])
    mission_id = await sync_followup_to_mission(
        symbol="BTC/USDT",
        record_id=diag["record_id"],
        question="如果跌破前低怎么办?",
        used_live=True,
        repo=cast(_MissionRepo, repo),
    )

    assert mission_id == "mis-000000000009"
    # 追问绝不新开 mission、也不推相位:它审问的是已存在的决策
    assert repo.created == []
    assert repo.transitions == []
    assert repo.activities == [
        (
            "mis-000000000009",
            "user",
            "pa_followup",
            {
                "record_id": diag["record_id"],
                "question": "如果跌破前低怎么办?",
                "used_live": True,
            },
        )
    ]
    # 只在该 symbol 的 market mission 里找
    assert repo.list_calls == [
        {"subject_type": "market", "subject_id": "BTC/USDT", "limit": 20}
    ]


@pytest.mark.asyncio
async def test_followup_without_owning_mission_is_skipped() -> None:
    # 手工 /analyze-trading 的 record 没有心跳投影 ⇒ 无 mission 可挂,静默跳过
    other = build_diagnosis(input_id="c" * 32, interval="1h")
    repo = _FakeRepo(missions=[_mission("mis-000000000010", [other])])
    mission_id = await sync_followup_to_mission(
        symbol="BTC/USDT",
        record_id="d" * 32,
        question="?",
        used_live=False,
        repo=cast(_MissionRepo, repo),
    )

    assert mission_id is None
    assert repo.activities == []


@pytest.mark.asyncio
async def test_followup_prefers_mission_where_record_is_latest() -> None:
    # 同一 record 出现在两条 mission 的日志里时,选 latest_diagnosis 指向它的那条,
    # 而不是把它当做过期条目留着的旧 mission
    diag = _diagnosis()
    newer = build_diagnosis(input_id="e" * 32, interval="1h")
    superseded: dict[str, Any] = {
        "id": "mis-000000000011",
        "context": {"diagnoses": [diag, newer], "latest_diagnosis": newer},
    }
    current = _mission("mis-000000000012", [diag])
    repo = _FakeRepo(missions=[superseded, current])
    mission_id = await sync_followup_to_mission(
        symbol="BTC/USDT",
        record_id=diag["record_id"],
        question="?",
        used_live=False,
        repo=cast(_MissionRepo, repo),
    )

    assert mission_id == "mis-000000000012"


# ---------------------------------------------------------------------------
# 真仓库链:上面走 _FakeRepo 只锁 connector 的决策逻辑,下面用真
# ResearchMissionRepository 跑一遍 —— 假的签名写歪时前面全绿也照样漏。
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_repo() -> AsyncIterator[ResearchMissionRepository]:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ResearchMissionRepository(db)


@pytest.mark.asyncio
async def test_followup_on_real_repo_lands_in_activity_stream(
    real_repo: ResearchMissionRepository,
) -> None:
    """心跳诊断建 mission → 追问挂上去,活动流里看得见,且不新开 mission。"""
    diag = _diagnosis()
    mission_id = await sync_diagnosis_to_mission(
        symbol="BTC/USDT", diagnosis=diag, repo=real_repo
    )
    assert mission_id is not None

    got = await sync_followup_to_mission(
        symbol="BTC/USDT",
        record_id=diag["record_id"],
        question="跌破前低怎么办?",
        used_live=False,
        repo=real_repo,
    )

    assert got == mission_id  # 挂到同一条,不是新开
    activity = await real_repo.list_activity(mission_id)
    followups = [a for a in activity if a["kind"] == "pa_followup"]
    assert len(followups) == 1
    assert followups[0]["actor"] == "user"
    assert followups[0]["detail"]["question"] == "跌破前低怎么办?"
    # 全库只有这一条 mission —— 追问没多开
    assert len(await real_repo.list_missions(subject_type="market")) == 1


@pytest.mark.asyncio
async def test_followup_on_real_repo_skips_never_projected_record(
    real_repo: ResearchMissionRepository,
) -> None:
    """手工 /analyze-trading 的 record 没有心跳投影 ⇒ 静默跳过,不留活动。"""
    diag = _diagnosis()
    mission_id = await sync_diagnosis_to_mission(
        symbol="BTC/USDT", diagnosis=diag, repo=real_repo
    )
    assert mission_id is not None

    got = await sync_followup_to_mission(
        symbol="BTC/USDT",
        record_id="f" * 32,
        question="?",
        used_live=False,
        repo=real_repo,
    )

    assert got is None
    activity = await real_repo.list_activity(mission_id)
    assert [a for a in activity if a["kind"] == "pa_followup"] == []
