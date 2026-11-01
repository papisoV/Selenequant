"""P0-4 reflection 闭环:ATR 自适应判定 + 到期验证仓储 + post_market 挂钩。

规格锚:internal-plan(判定公式+验收数字)。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "storage"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "daily-cycle"))

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.experiments import ExperimentRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_storage.verification import (
    effective_threshold,
    judge,
)


class TestEffectiveThreshold:
    """规格验收锚 1:阈值函数的 k/下限/上限三重钳制。"""

    def test_normal_band_uses_raw_atr(self) -> None:
        assert effective_threshold(3.0, "long") == pytest.approx(3.0)
        assert effective_threshold(3.0, "short") == pytest.approx(3.0)

    def test_floor_protection(self) -> None:
        # 0.1% ATR 低于成本估计 → 抬到 0.2% 下限。
        assert effective_threshold(0.1, "long") == pytest.approx(0.2)

    def test_ceiling_clamp(self) -> None:
        # 20% 极端波动 → 钳到 15% 上限。
        assert effective_threshold(20.0, "short") == pytest.approx(15.0)

    def test_hold_direction_scales_by_k_hold(self) -> None:
        assert effective_threshold(3.0, "hold") == pytest.approx(2.25)


class TestJudge:
    """规格验收锚 2:方向单与 HOLD 的判定规则。"""

    def test_long_correct_when_signed_return_beats_threshold(self) -> None:
        assert judge("long", entry=100.0, exit_price=104.0, threshold_pct=3.0)

    def test_long_wrong_when_return_below_threshold(self) -> None:
        assert not judge("long", entry=100.0, exit_price=102.0, threshold_pct=3.0)

    def test_short_correct_when_price_drops_past_threshold(self) -> None:
        assert judge("short", entry=100.0, exit_price=95.0, threshold_pct=3.0)

    def test_short_wrong_when_price_moves_against(self) -> None:
        assert not judge("short", entry=100.0, exit_price=105.0, threshold_pct=3.0)

    def test_hold_correct_inside_band(self) -> None:
        assert judge("hold", entry=100.0, exit_price=101.0, threshold_pct=2.0)

    def test_hold_wrong_when_breakout_exceeds_band(self) -> None:
        assert not judge("hold", entry=100.0, exit_price=103.0, threshold_pct=2.0)


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ExperimentRepository(db)


def _backdate(row: dict, days: int) -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row["created_at"] = created


class TestDueVerification:
    """规格验收锚 3:list_due 只回超期未验证行。"""

    @pytest.mark.asyncio
    async def test_only_overdue_unverified_rows(self, repo: ExperimentRepository):
        old_id = await repo.create({"name": "老实验"})
        fresh_id = await repo.create({"name": "新实验"})

        # 直改 created_at 模拟时间流逝(create 不收该字段);只 backdate 老实验,
        # 新实验保持当前时间,验证 list_due 只捞超期行。
        await repo._db.execute(
            "UPDATE experiments SET created_at = ? WHERE id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
                old_id,
            ),
        )
        verified_id = await repo.create({"name": "已验证"})
        await repo.record_verification(
            verified_id,
            actual_return_pct=1.5,
            atr_pct=2.0,
            was_correct=True,
            code_hash="abc",
            engine_version="1",
            verified_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        )

        due = await repo.list_due(
            now_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            horizon_days=7,
        )
        ids = {row["id"] for row in due}
        assert old_id in ids
        assert fresh_id not in ids
        assert verified_id not in ids

    @pytest.mark.asyncio
    async def test_record_verification_roundtrip_and_idempotence(
        self, repo: ExperimentRepository
    ):
        exp_id = await repo.create({"name": "待验证"})
        ok = await repo.record_verification(
            exp_id,
            actual_return_pct=-4.2,
            atr_pct=3.0,
            was_correct=False,
            code_hash="deadbeef",
            engine_version="bt-2",
            verified_at_ms=1711000000000,
        )
        assert ok is True
        row = await repo.get(exp_id)
        assert row is not None, "验证后记录应存在"
        assert row["actual_return_pct"] == pytest.approx(-4.2)
        assert row["atr_pct_at_analysis"] == pytest.approx(3.0)
        assert row["was_correct"] == 0
        assert row["code_hash"] == "deadbeef"
        assert row["engine_version"] == "bt-2"
        assert row["verified_at"] is not None

        again = await repo.record_verification(
            exp_id,
            actual_return_pct=9.9,
            atr_pct=3.0,
            was_correct=True,
            code_hash="ff",
            engine_version="bt-9",
            verified_at_ms=1712000000000,
        )
        assert again is False, "已验证行拒绝重复写入"


class TestPostMarketHook:
    """规格验收锚 5:verifier 注入与 None 兼容。"""

    @pytest.mark.asyncio
    async def test_verifier_results_land_in_metrics_and_lessons(self):
        from selene_space_daily_cycle.phases.post_market import PostMarketPhase
        from selene_space_daily_cycle.types import WorkflowContext

        async def stub_verifier() -> dict:
            return {"verified_count": 2, "correct_count": 1}

        phase = PostMarketPhase(experiment_verifier=stub_verifier)
        outcome = await phase.execute(
            trading_date=datetime.now(timezone.utc).date(), context=WorkflowContext()
        )
        assert outcome.metrics.get("verified_count") == 2
        assert any(
            "到期验证" in lesson
            for lesson in outcome.artifacts["post_market_report"].lessons
        )

    @pytest.mark.asyncio
    async def test_none_verifier_keeps_legacy_behavior(self):
        from selene_space_daily_cycle.phases.post_market import PostMarketPhase
        from selene_space_daily_cycle.types import WorkflowContext

        phase = PostMarketPhase()
        outcome = await phase.execute(
            trading_date=datetime.now(timezone.utc).date(), context=WorkflowContext()
        )
        assert outcome.status.value == "success"
        assert "verified_count" not in outcome.metrics
