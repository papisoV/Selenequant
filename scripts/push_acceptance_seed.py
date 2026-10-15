"""验收种子推进器 — 把演示 Mission 推到拍板态,让四动作在演示环境可达。

背景(2026-09-12 用户链路模拟诊断):
全库 17 个 Mission 全卡 investigation;investigation → needs_input("Selene 调查完成
推送判定")当前无任何系统能做,导致四动作(Approve/More evidence/Redirect/Park)
在整个产品里不可见。本脚本模拟该系统行为,把验收种子推到 needs_input。

幂等:phase 已是 needs_input/validation/resolved 时跳过,不重复 transition。

用法:
    call activate.bat
    python scripts/push_acceptance_seed.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 副本根 = 本文件上两级(scripts/ 的父目录)
REPO_ROOT = Path(__file__).resolve().parents[1]
STORAGE_PKG = REPO_ROOT / "packages" / "storage"
sys.path.insert(0, str(STORAGE_PKG))

from selene_space_storage import (  # noqa: E402
    ALL_MIGRATIONS,
    AsyncSQLiteManager,
    MigrationRunner,
)
from selene_space_storage.repositories.research_missions import (  # noqa: E402
    ResearchMissionRepository,
)

SEED_MISSION_ID = "mis-3adeb596e736"
SEED_NOTE = "调查完成 — 判定已推送:回到声明配置重新验证(BTC 4h 段重跑),等待你的决定"


async def main() -> None:
    db = AsyncSQLiteManager(migration_runner=MigrationRunner(ALL_MIGRATIONS))
    await db.connect()
    try:
        repo = ResearchMissionRepository(db)
        mission = await repo.get(SEED_MISSION_ID)
        if mission is None:
            print(f"未找到验收种子 Mission {SEED_MISSION_ID}(种子未灌库?)")
            return
        phase = mission["phase"]
        print(f"验收种子 {SEED_MISSION_ID} 当前 phase = {phase}")
        if phase == "needs_input":
            print("跳过 — 已处于待拍板态(幂等),四动作可直接演示。")
            return
        if phase == "resolved":
            print(
                "跳过 — 已结案(resolved 是终态,无法回拍板态)。重新灌验收种子后再运行。"
            )
            return
        # LEGAL_TRANSITIONS:investigation/validation 可直达 needs_input;
        # parked 只能先回 investigation 再进 needs_input。
        if phase == "parked":
            await repo.transition(
                SEED_MISSION_ID,
                "investigation",
                actor="selene",
                note="演示复位 — 从停放回到调查",
                detail={"action": "demo_reset"},
            )
            print("两跳推进:parked → investigation")
        updated = await repo.transition(
            SEED_MISSION_ID,
            "needs_input",
            actor="selene",
            note=SEED_NOTE,
            detail={"action": "complete_investigation"},
        )
        print(f"已推进 → {updated['phase']}")
        print("四动作(Approve/More evidence/Redirect/Park)现可在 Mission 详情页拍板。")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
