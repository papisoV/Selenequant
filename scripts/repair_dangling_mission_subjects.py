"""Repair strategy-typed missions whose ``subject_id`` is not a registered strategy.

2026-10-13: audit rule ``mission_subject_dangling``
(``repositories/research_graph/integrity.py``) flagged exactly two rows in the
shared main DB (``~/.selenespace/data/selenspace.db``):

- ``mis-b2f9056975c5`` — real user research thread ("BTC 1h funding 延迟衰减").
  Its subject was the probe name ``entry-delay-ic-probe``: the question's
  ``anchor.strategy_ids`` carried the probe name and the promote pipeline
  blindly took ``[0]`` (L2 now refuses this).
- ``mis-3adeb596e736`` — a ``verification-seed`` demo ("配置漂移裁决演示")
  whose subject is the backtest id ``bt-b62d32ac``.

Neither subject is a registered strategy, so the strategy cockpit State
endpoint (``GET /research/strategy/{id}/state``) 404s for them. L1
(``ResearchMissionRepository._validate_subject_id``) now blocks new rows like
this; this script repairs the two that predate the gate.

Remedy = **reclassify the subject**, not delete the mission. The gate's own
error message prescribes it: "若这是探针/回测/自由课题,请改用 event 或 system
主体". We use ``system`` (free-form subject; precedent ``mis-592f0ed6a2d1``
with subject ``batch3-live-verify``). The original ``subject_id`` is preserved
verbatim — ``system`` subjects are unconstrained, so nothing is lost, and the
research thread stays reachable through ``context.question_id`` /
``context.revision_id``. An ``mission_activity`` row records the change.

Fail-closed and idempotent: every target row is re-read and must match the
expected pre-state before its UPDATE; a row already reclassified is skipped.

Usage:
    python scripts/repair_dangling_mission_subjects.py          # dry run
    python scripts/repair_dangling_mission_subjects.py --apply  # write
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("packages/storage", "packages/shared-types"):
    _p = str(_REPO_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from selene_space_storage.repositories.research_graph import (  # noqa: E402
    check_graph_integrity,
)
from selene_space_storage.repositories.research_missions import (  # noqa: E402
    ResearchMissionRepository,
    _dump_json,
    _now,
)
from selene_space_storage.sqlite import (  # noqa: E402
    DEFAULT_DB_PATH,
    AsyncSQLiteManager,
)

# (mission_id, expected current subject_id, new subject_type, extra context).
# The subject_id is kept verbatim, so only the type moves. Asserting the current
# subject_id means a row that drifted (or was already repaired by hand) is
# caught, not clobbered.
_TARGETS: tuple[tuple[str, str, str, dict], ...] = (
    (
        "mis-b2f9056975c5",
        "entry-delay-ic-probe",
        "system",
        {
            # Real user research thread; the probe name has no strategy behind
            # it, so there is nothing to resolve to. Thread stays reachable via
            # context.question_id / context.revision_id.
        },
    ),
    (
        "mis-3adeb596e736",
        "bt-b62d32ac",
        "system",
        {
            # The backtest id resolves to a real strategy; recorded so the link
            # is not lost by the reclassification.
            "resolvable_strategy_id": "strat-6395c312",
        },
    ),
)

_REASON = (
    "strategy subject_id 不是已注册策略 —— 探针名/回测 id 被写进 strategy 主体,"
    "驾驶舱 State 端点必然 404。按 L1 门的指引改判为 system(自由主体),"
    "subject_id 原样保留。修复脚本 scripts/repair_dangling_mission_subjects.py。"
)


async def _dangling_ids(db) -> set[str]:
    report = await check_graph_integrity(db)
    return {
        v["object_id"]
        for v in report["violations"]
        if v["rule"] == "mission_subject_dangling"
    }


async def repair(db, apply: bool, targets=_TARGETS) -> tuple[int, int]:
    """Reclassify the target missions' subjects. Returns ``(changed, remaining)``.

    ``remaining`` is the number of dangling strategy missions left after the
    pass. Pure over ``db`` so it can be exercised on an in-memory database.
    """
    before = await _dangling_ids(db)
    print(f"before: {len(before)} dangling strategy mission(s) {sorted(before)}")
    print()

    repo = ResearchMissionRepository(db)
    changed = 0
    for mission_id, expect_subject, new_type, extra in targets:
        mission = await repo.get(mission_id)
        if mission is None:
            print(f"[SKIP] {mission_id}: not found")
            continue
        if mission["subject_type"] != "strategy":
            print(
                f"[SKIP] {mission_id}: subject_type already "
                f"{mission['subject_type']!r} (idempotent)"
            )
            continue
        if mission["subject_id"] != expect_subject:
            raise ValueError(
                f"{mission_id}: subject_id is {mission['subject_id']!r}, "
                f"expected {expect_subject!r} — 拒绝盲改"
            )

        print(
            f"[{'APPLY' if apply else 'DRY '}] {mission_id}: "
            f"strategy/{mission['subject_id']}  ->  {new_type}/{mission['subject_id']}"
        )
        if not apply:
            changed += 1
            continue

        # All writes in one transaction — a crash between them would otherwise
        # leave a reclassified row with no audit trail.
        merged_context = {
            **(mission["context"] if isinstance(mission["context"], dict) else {}),
            "subject_reclassified": {
                "from_type": "strategy",
                "to_type": new_type,
                "subject_id": mission["subject_id"],
                "reason": _REASON,
                **extra,
            },
        }
        detail = {
            "from_type": "strategy",
            "to_type": new_type,
            "subject_id": mission["subject_id"],
            "reason": _REASON,
            **extra,
        }
        now = _now()
        async with db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE research_missions SET subject_type = ?, "
                "context_json = ?, updated_at = ? "
                "WHERE id = ? AND subject_type = 'strategy' AND subject_id = ?",
                (
                    new_type,
                    _dump_json(merged_context),
                    now,
                    mission_id,
                    expect_subject,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"{mission_id}: guarded UPDATE affected "
                    f"{cursor.rowcount} rows, expected 1 — aborting"
                )
            await conn.execute(
                "INSERT INTO mission_activity "
                "(mission_id, occurred_at, actor, kind, detail_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (mission_id, now, "system", "subject_reclassified", _dump_json(detail)),
            )
        changed += 1

    print()
    after = await _dangling_ids(db)
    print(f"after:  {len(after)} dangling strategy mission(s) {sorted(after)}")
    print()
    print(f"{'applied' if apply else 'would apply'}: {changed} row(s)")
    return changed, len(after)


async def _run(apply: bool) -> int:
    print(f"DB: {DEFAULT_DB_PATH}")
    print(f"mode: {'APPLY' if apply else 'DRY-RUN'}")
    print()
    async with AsyncSQLiteManager() as db:
        _, remaining = await repair(db, apply)
        if apply and remaining:
            print(f"[WARN] still dangling after repair: {remaining}")
            return 1
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
