"""最小召回回路 —— §1④ 分界句的第一个用户可见出口(路线图 W1-A / Task 5C)。

问题:**Selene 到底有没有"让上一段历史改变下一次判断"?**

回答它的最弱充分条件是一条只有 Question + Revision + Edge + Episode + recall
的回路:``round1`` 在**进程 A** 里写库然后退出;``round2`` 在**另一个进程 B**
里只开同一个库文件,把取回来的东西打印出来。

为什么不许在同一个进程里演一遍(§11.4):同进程里"我记得"可以靠变量,
跨进程只能靠库。**承载证据价值的是进程隔离,不是日历天数** —— 所谓"等一个
月看看"证的是一件更弱的事。

为什么 round1 必须同时落两类候选:证据链走得到 Episode / Evidence 的才算
Learned Experience,走不到的只能算 Research Record(§6.10 ⑤)。只落一条
revision 路径,就绕开了 ``recall()`` 唯一真正难的那一件事 —— 分层。那样
变绿,证的是"取回"而不是"取回**并分得清**"。

跑法::

    python scripts/recall_smoke.py round1 --db /tmp/w1a.db            # 进程 A
    python scripts/recall_smoke.py round2 --db /tmp/w1a.db            # 进程 B
    python scripts/recall_smoke.py round2 --db /tmp/w1a.db --no-recall  # 对照组

**假绿识别(路线图原文)**:脚本里出现 round1 的返回值 → 违 §11.4;
账本 id 硬编码 → 违 §11.4;只断言了 revision 路径就宣布通过 → 违 §6.10 分层。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# ``selene_space_storage.__init__`` 会连带 import repositories.backtests,
# 那里引 selene_space_types —— 脚本不像 pytest 有 pyproject 的 pythonpath 兜底。
for _pkg in ("storage", "shared-types"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / _pkg))

from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.repositories.research_graph import (  # noqa: E402
    EpisodeRepository,
    EvidenceRepository,
    HypothesisRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
    recall,
)
from selene_space_storage.sqlite import AsyncSQLiteManager  # noqa: E402

# 两轮共享的**情境**常量。它是"同一种研究情境",不是 round1 的产物 ——
# round1 写下的 id 只能经由 db 文件流到 round2,这条常量和它无关。
# 注意它必须是常量而不是"从库里读 round1 的问题":那样 round2 的情境就成了
# round1 的输出,跨 Question 召回这件事反而被绕过去了。
ANCHOR = {"strategy_ids": ["strat-keltner-sol"], "intervals": ["4h"]}

SUBJECT_TYPE = "strategy"
SUBJECT_ID = "strat-keltner-sol"


# ---------------------------------------------------------------------------
# 进程 A —— 写库后退出
# ---------------------------------------------------------------------------


async def _round1(db: AsyncSQLiteManager) -> None:
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)

    qid = await questions.create(
        title="Keltner V2 在 SOL 4h 上连续亏损,是策略失效还是环境变了?",
        body="2026-08 实跑段七笔全亏,V2 配置一字未改。",
        anchor=ANCHOR,
    )

    # ① 走得到 Episode / Evidence 的那一类
    episode_id = await EpisodeRepository(db).create(
        subject_type=SUBJECT_TYPE,
        subject_id=SUBJECT_ID,
        title="Keltner V2 @ SOL 4h — 2026-08 实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T23:59:59+00:00",
        recorded={"trades": 7, "pnl": -0.0695},
        occurred_at="2026-08-31T23:59:59+00:00",
    )
    evidence_id = await EvidenceRepository(db).create(
        episode_id=episode_id,
        mode="RECORDED",
        payload={"trades": 7, "pnl": -0.0695},
        source_ref="paper-2026-08",
    )
    # 这一条 addresses 边是**引用验证**要的(引用的对象必须与本问题相连),
    # 不是证据链要的 —— 证据链走的是 cited,与边无关。
    await edges.add_fact_edge(("evidence", evidence_id), ("question", qid), "addresses")

    revision = await questions.add_revision(
        qid,
        "七笔全亏集中在 08-19 之后,同期 ATR 抬升 1.8 倍。判断:先归因到环境,"
        "不急着改参数。",
        rationale=["同段 ATR 抬升,与'参数失效'这一解释无法区分"],
        cited=(evidence_id,),
    )

    # ② 走不到的那一类:只靠 addresses 挂在问题下的假设。
    #    addresses 刻意不算证据链 —— 它只是"进了这个问题的研究世界"的凭证。
    hypothesis_id = await HypothesisRepository(db).create(
        "亏损主要由 regime shift 导致,与参数无关"
    )
    await edges.add_fact_edge(
        ("hypothesis", hypothesis_id), ("question", qid), "addresses"
    )

    print(f"第一段研究完成 —— 写库后本进程退出(question={qid})")
    print(f"写入: {revision['id']}")
    print(f"写入: {hypothesis_id}")


# ---------------------------------------------------------------------------
# 进程 B —— 只开库文件
# ---------------------------------------------------------------------------


async def _round2(db: AsyncSQLiteManager, *, use_recall: bool) -> None:
    # 第二段研究是一个**新问题**,情境相同(anchor 相同)。
    # 这里唯一从外部拿到的输入就是 db 文件路径。
    qid = await ResearchQuestionRepository(db).create(
        title="同一策略换到新窗口,要不要按 08 的结论先归因环境?",
        body="新窗口 2026-09,沿用 08 段的观察。",
        anchor=ANCHOR,
    )
    print(f"第二段研究(新问题 {qid})—— 本进程只拿到一个 db 文件路径")
    print("账本(取回的历史):")

    if not use_recall:
        # 对照组:不调用 recall。账本为空是**因为它没去取**,不是因为库空了 ——
        # 这两件事必须能分开,否则"账本为空"根本不算证据。
        print("本次未取回历史")
        print("(对照组:本进程未调用 recall,除该开关外与默认运行完全相同)")
        return

    found = await recall(db, question_id=qid)
    for cand in found:
        print(f"  [{cand['recall_class']}] {cand['id']} — {cand['text']}")
    if not found:
        print("本次未取回历史")
        return

    by_class: dict[str, int] = {}
    for cand in found:
        key = str(cand["recall_class"])
        by_class[key] = by_class.get(key, 0) + 1
    print(f"本次取回 {len(found)} 条历史")
    print("分层覆盖: " + " / ".join(f"{k} ×{v}" for k, v in sorted(by_class.items())))


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("round", choices=("round1", "round2"))
    parser.add_argument("--db", required=True, help="SQLite 库文件路径")
    parser.add_argument(
        "--no-recall",
        action="store_true",
        help="对照组:round2 不调用 recall(round1 无此开关)",
    )
    args = parser.parse_args()

    if args.round == "round1" and args.no_recall:
        parser.error("--no-recall 只对 round2 有意义")

    async def run() -> None:
        runner = MigrationRunner(ALL_MIGRATIONS)
        async with AsyncSQLiteManager(args.db, migration_runner=runner) as db:
            if args.round == "round1":
                await _round1(db)
            else:
                await _round2(db, use_recall=not args.no_recall)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
