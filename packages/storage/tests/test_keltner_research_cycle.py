"""§11 验收判据的机器化版本。

判据原文:**第二次回答必须真的利用第一次研究留下的东西,并因此与第一次
回答不同。**

四个测试各自压住一件事,合起来才是完整判据:

- ``test_round2_needs_no_in_memory_handoff`` —— **进程隔离的可证伪替身**。
  round2 的输入只有一个 db 句柄;问题、第一版回答、证据全部从磁盘自己找。
  这个测试一旦通过,"第二轮靠内存变量"这条假绿路径就断了。
- ``test_round2_cites_what_recall_returned`` —— 引用是 **recall 供给的**,
  不是手写的。同时断言它引用了第一轮的 revision(除了 recall,round2
  没有任何别的渠道拿到这个 id)。
- ``test_control_group_difference_is_attributable_to_recall`` —— **对照组**。
  同一条链跑两遍(有 recall / 无 recall),差异只能归因给 recall。
  **没有对照组,"不同"可能来自新证据或任何别的东西。**
- ``test_round2_leaves_clean_graph`` —— 循环跑完之后图仍然干净,而且
  Episode / Evidence 的计数与预期一致(§9.5 九条规则没有一条被踩)。

走不通 = §11.3 的 gate 不成立(实现没做完,去修);
机械通过但用户无感 = grade 不成立(那才是证伪)。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


def _load_script():
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import keltner_research_cycle

    return keltner_research_cycle


@pytest.mark.asyncio
async def test_round2_needs_no_in_memory_handoff(db):
    """round2 拿到的东西必须是它自己从磁盘找到的。

    刻意**丢掉** round1 的返回值 —— 如果 round2 能跑通,就证明它没有
    依赖任何内存接力。这是"过一段真实时间"在测试里的替身。
    """
    cycle = _load_script()
    await cycle.run_round1(db)
    # 注意:上面的返回值被丢弃了,没有传给 run_round2

    result = await cycle.run_round2(db)

    assert result["question_id"], "round2 必须自己从库里找到问题"
    assert result["revision"]["seq"] == 2
    assert result["recall"]["enabled"] is True
    assert result["recall"]["count"] > 0, "recall 必须真的取回了东西"


@pytest.mark.asyncio
async def test_round2_cites_what_recall_returned(db):
    """引用是 recall 供给的,而且含第一轮留下的成果。"""
    cycle = _load_script()
    round1 = await cycle.run_round1(db)

    result = await cycle.run_round2(db)
    cited = set(result["revision"]["cited"])

    assert (
        round1["revision"]["id"] in cited
    ), "第二轮必须引用第一版回答 —— 而 round2 只有 recall 一条渠道能拿到这个 id"
    assert cited, "引用不能是空的"


@pytest.mark.asyncio
async def test_control_group_difference_is_attributable_to_recall(db):
    """对照组:同一条链,只有 recall 开 / 关不同。

    差异出现 → 差异可归因给 recall(§11.4)。
    差异不出现 → recall 是摆设,Memory 就是档案柜。
    """
    cycle = _load_script()
    round1 = await cycle.run_round1(db)

    armed = await cycle.run_round2(db)
    armed_cited = set(armed["revision"]["cited"])

    # 2B:另起一条完全相同的链,唯一区别是 --no-recall
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as blind_db:
        await cycle.run_round1(blind_db)
        blind = await cycle.run_round2(blind_db, recall_enabled=False)

    assert blind["recall"]["enabled"] is False
    assert blind["recall"]["count"] == 0
    assert blind["revision"]["cited"] == [], "没有召回就不该有引用(它无从得知该引谁)"
    assert round1["revision"]["id"] not in blind["revision"]["cited"]

    # 这就是**可归因的差异**
    assert armed_cited != set(blind["revision"]["cited"])
    assert armed["revision"]["content"] != blind["revision"]["content"]


@pytest.mark.asyncio
async def test_round2_leaves_clean_graph(db):
    cycle = _load_script()
    from selene_space_storage.repositories.research_graph import check_graph_integrity

    await cycle.run_round1(db)
    await cycle.run_round2(db)

    report = await check_graph_integrity(db)
    assert report["ok"] is True, report["violations"]
    assert report["counts"]["episodes"] == 1
    assert report["counts"]["evidence"] == 3
