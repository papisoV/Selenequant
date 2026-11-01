"""Archive duplicate open missions that share one (subject, question).

2026-10-22: TODAY 页 mission 区实测 35 张卡里 ``PA diagnosis BTC/USDT 1h``
一个标题出现 21 次。根因是旧版 ``scripts/run_pa_diagnosis.py`` 每次跑都无条件
``repo.create()``(去重守卫 ``find_open_by_question`` 后来才加,只防新增)。
本脚本清理存量:每组 (subject_type, subject_id, question) 里**只保留 worker
口径的正典那一条**,其余转 ``parked``。

保留口径与 ``ResearchMissionRepository.find_open_by_question`` 逐字一致:
非终态行按 ``updated_at DESC`` 排第一的那条。worker 每次跑都刷正典行的
``updated_at``,所以保留的永远是 worker 正在写的那条;归档不会打断活工作流。

归档 = ``transition(..., "parked")``,合法状态机边(needs_input/validation 都
允许 → parked),可逆(parked → investigation),不删数据、不动 schema;每条
归档写一条 ``mission_activity`` 记录指向正典 id。

Fail-closed 与幂等:
- 每条目标在 transition 前重读,确认仍是非终态、且**仍不是**组内最新 —— 若
  worker 恰好在读与写之间把它刷成最新,跳过并在输出里说明。
- 单组只剩 1 条时无操作,重复跑安全。

Usage:
    python scripts/archive_duplicate_missions.py          # dry run
    python scripts/archive_duplicate_missions.py --apply  # write
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("packages/storage", "packages/shared-types"):
    _p = str(_REPO_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from selene_space_storage.repositories.research_missions import (  # noqa: E402
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import (  # noqa: E402
    DEFAULT_DB_PATH,
    AsyncSQLiteManager,
)

_TERMINAL = ("resolved", "parked")

_REASON = (
    "同一 (subject, question) 下的重复开案:去重守卫 find_open_by_question 上线前"
    "旧版 run_pa_diagnosis.py 无条件 create 的存量。保留 worker 口径正典,"
    "其余归档为 parked。脚本 scripts/archive_duplicate_missions.py。"
)


def _group_key(mission: dict) -> tuple[str, str, str]:
    return (mission["subject_type"], mission["subject_id"], mission["question"])


async def _open_groups(repo: ResearchMissionRepository) -> dict[tuple, list[dict]]:
    """All non-terminal missions, grouped, each group newest-first (worker order)."""
    rows = await repo.list_missions(limit=10_000)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for m in rows:
        if m["phase"] in _TERMINAL:
            continue
        groups[_group_key(m)].append(m)
    for g in groups.values():
        g.sort(key=lambda m: (m["updated_at"] or "", m["id"]), reverse=True)
    return groups


async def archive_duplicates(db, apply: bool) -> tuple[int, int]:
    """Archive every non-canonical open duplicate. Returns (archived, remaining)."""
    repo = ResearchMissionRepository(db)

    print(f"DB: {DEFAULT_DB_PATH}")
    print(f"mode: {'APPLY' if apply else 'DRY-RUN'}")
    print()

    groups = await _open_groups(repo)
    total_open = sum(len(g) for g in groups.values())
    dup_groups = {k: g for k, g in groups.items() if len(g) > 1}
    planned = sum(len(g) - 1 for g in dup_groups.values())
    print(
        f"open missions: {total_open} in {len(groups)} group(s); "
        f"{len(dup_groups)} group(s) with duplicates; would archive {planned}"
    )
    print()

    archived = 0
    skipped = 0
    for key, group in sorted(dup_groups.items()):
        subject_type, subject_id, question = key
        canon = group[0]
        print(
            f"[GROUP] {subject_type}/{subject_id}  q={question[:40]}  ({len(group)} open)"
        )
        print(f"  KEEP  {canon['id']}  {canon['phase']}  upd={canon['updated_at']}")
        for m in group[1:]:
            # Re-read: worker may have advanced this row between planning and
            # writing. Only archive if it is still open and still non-canonical.
            current = await repo.get(m["id"])
            if current is None or current["phase"] in _TERMINAL:
                print(f"  [SKIP] {m['id']}: phase now {current and current['phase']}")
                skipped += 1
                continue
            if (current["updated_at"] or "") >= (canon["updated_at"] or ""):
                print(
                    f"  [SKIP] {m['id']}: updated_at advanced to {current['updated_at']} "
                    f"(worker moved the canon) — re-run to replan"
                )
                skipped += 1
                continue
            print(
                f"  [{'APPLY' if apply else 'DRY '}] {m['id']}: {m['phase']} -> parked"
            )
            archived += 1
            if apply:
                # 先打维护性标记再转相:context 上的 archive.duplicate_of 让
                # TODAY 的 settled 查询在 SQL 层就把它们排除 —— 27 条归档一次
                # 写入会占满 parked 的 LIMIT 窗口,把 WORLD CHANGED 挤空。
                await repo.update_context(
                    m["id"],
                    {
                        "archive": {
                            "duplicate_of": canon["id"],
                            "reason": "duplicate_open_mission",
                            "maintenance": True,
                        }
                    },
                )
                await repo.transition(
                    m["id"],
                    "parked",
                    actor="system",
                    detail={
                        "reason": "duplicate_open_mission",
                        "note": _REASON,
                        "canonical_mission_id": canon["id"],
                        "maintenance": True,
                    },
                )
        print()

    if apply:
        after = await _open_groups(repo)
        remaining = sum(len(g) for g in after.values())
        still_dup = {k: len(g) for k, g in after.items() if len(g) > 1}
        print(f"open missions after: {remaining}")
        if still_dup:
            print(f"[WARN] groups still duplicated: {still_dup}")
    print(
        f"{'archived' if apply else 'would archive'}: {archived} row(s); skipped {skipped}"
    )
    return archived, 0


async def _run(apply: bool) -> int:
    async with AsyncSQLiteManager() as db:
        await archive_duplicates(db, apply)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    raise SystemExit(main())
