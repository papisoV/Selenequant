"""Keltner 研究循环 —— §11 验收判据的可执行证明。

判据(§11.3):**第二次回答必须真的利用第一次研究留下的东西,并因此与
第一次回答不同。**

这个脚本的每一轮都必须能独立跑在**一个空进程**里:

    round1  写库 → 退出
    ─────── 进程边界 ───────
    round2  只开库文件,不 import round1 的任何东西
            → recall() 取回历史 → 第二版回答

这不是洁癖。两轮跑在同一个函数调用里时,``round1["id"]`` 是局部变量,
三个验收标志全部能由内存满足 —— **持久化完全坏掉也能通过**,那样测出来
的只是"持久化可用",不是"研究有了连续性"。

用法:

    python scripts/keltner_research_cycle.py round1 --db <path>
    python scripts/keltner_research_cycle.py round2 --db <path>
    python scripts/keltner_research_cycle.py round2 --db <path> --no-recall   # 对照组 2B
    python scripts/keltner_research_cycle.py acceptance                       # 全自动

不带 ``--db`` 时打到真实库 ``~/.selenespace/data/selenespace.db``。

``acceptance`` 的出口是**人话 diff**(``_render_diff``),不是 id 列表 ——
理由与本次无法执行的比较都写在下面的 ``LIMITS`` 里,**不要默认它成立**。
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
# 两个包都要进 path。只放 storage 会在 import selene_space_storage 时炸 ——
# 它的 __init__ 连带 import repositories.backtests,那里引 selene_space_types,
# 而脚本不像 pytest 有 pyproject 的 pythonpath 兜底。
# (实测:`sys.path` 只有 packages/storage → ModuleNotFoundError: selene_space_types)
for _pkg in ("storage", "shared-types"):
    sys.path.insert(0, str(_REPO_ROOT / "packages" / _pkg))

from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.repositories.research_graph import (  # noqa: E402
    DecisionRepository,
    EpisodeRepository,
    EvidenceRepository,
    ExperimentRepository,
    HypothesisRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
    check_graph_integrity,
    recall,
)
from selene_space_storage.sqlite import AsyncSQLiteManager  # noqa: E402

STRATEGY_ID = "strat-6395c312"
BACKTEST_ID = "bt-b62d32ac"
QUESTION_TITLE = "Keltner V2 在 SOL 4h 上实盘亏损的根因是什么?"

DRIFT_STATEMENT = "亏损主要由配置漂移导致"
REGIME_STATEMENT = "亏损主要由 regime shift 导致"

ROUND1_ANSWER = (
    "初步判断:亏损主要由**配置漂移**导致 —— 声明的 BTC/USDT 1h 与实际运行的 "
    "SOL/USDT 4h 不一致,策略参数与市场状态不匹配。框架本身未见失效迹象。"
)

# 2A:取回了第一版判断 → 回答里能对照它
ROUND2_WITH_PRIOR_PREFIX = (
    "修正判断:配置漂移是**必要条件但不是充分条件**。按声明配置重跑后 OOS 仍为负,"
    "而 tick 重算显示实际亏损 (-11.5%) 比当时记录的 (-6.95%) 更大,"
    "且该段处于 regime shift 后半程 —— 二者共同解释亏损。"
    "对照第一版(『"
)
ROUND2_WITH_PRIOR_SUFFIX = (
    "』):该判断被**限定**而非推翻。结论:需重验证,并把 regime 条件写入策略前置条件。"
)

# 2B:没有召回 —— 回答里明确说不出与既有判断的对照
ROUND2_BLIND = (
    "修正判断:按声明配置重跑后 OOS 仍为负,而 tick 重算显示实际亏损 (-11.5%) "
    "比当时记录的 (-6.95%) 更大,且该段处于 regime shift 后半程 —— 二者共同解释亏损。"
    "(本版未取回此前的研究历史,因此不含与既有判断的对照。)"
)

# ---------------------------------------------------------------------------
# §11.4 替代比较的限制 —— **必须写在案,不得默认它成立**
# ---------------------------------------------------------------------------

LIMITS = """\
【本次无法执行的比较,以及替代物】
**§11.4 指定的比较对象(Hypothesis / Experiment / Decision)在本计划 Task 9 的
两个 arm 中相同** —— 决策无条件写死(计划 L5017)、假设只由 round1 产生。因此
本次**无法**执行 Canon 指定的那个比较,**以「cited 集合差异 + content 文本差异」
作为替代比较对象**,并把这条替代记录在此,而非默认它成立。

【另外两条必须写在案的限制】
1. Task 9 的第二版回答文本是**写死的常量**(ROUND2_*)。
2. attributable_to_recall 四项里 arms_differ_in_content **恒真**,贡献零证据 ——
   真正承重的只有 round2a_cites_round1 与 round2b_is_blind 两项。

【为什么不把 round2 的 decision 改成依 recall_enabled 取值】
那等于往演示器里再写死一个答案("取回历史 → 结论变成 X")。Task 9 已经有三个
写死常量;再加一个只是让脚本更绿,不会让它更像真实测量。真实的那个比较等到
W2(用户自己写第二版)与 W3(真实研究里的引用路径)。

**gate 成立 ≠ 定位成立。** 本脚本全绿只证明:2A 的 cited 里有 round1 的 id,
2B 的 cited 为空。它**不**证明 §11.4 指定的下游对象比较成立 —— 那两个 arm 的
下游对象完全相同。grade 的第一个真实证据出现在 W2-J3。
"""


async def find_question_id(db: Any, title: str) -> str | None:
    """按标题找问题 —— round2 靠这个从磁盘上把自己接回来。"""
    rows = await db.execute(
        "SELECT id FROM research_questions WHERE title = ? ORDER BY opened_at DESC LIMIT 1",
        (title,),
    )
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------------------
# round1 —— 当时我们知道什么
# ---------------------------------------------------------------------------


async def run_round1(db: Any) -> dict[str, Any]:
    """立问题 → 一段经历 → **当时唯一的观察** → 两条假设 → 实验 → 第一版回答。

    这一步之后进程就该退出。**返回的 id 只用于打印,不允许被 round2 使用。**
    round2 若用到其中任何一个,进程隔离就白做了。
    """
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)
    episodes = EpisodeRepository(db)
    evidence = EvidenceRepository(db)
    hypotheses = HypothesisRepository(db)
    experiments = ExperimentRepository(db)

    question_id = await questions.create(
        title=QUESTION_TITLE,
        body="实跑配置与声明配置不一致,需判断是配置漂移、框架失效,还是市场状态变化。",
        anchor={
            "strategy_ids": [STRATEGY_ID],
            "intervals": ["4h"],
            "regimes": ["trend", "range"],
        },
    )

    # 一段已发生的经历(Episode 不可改,§8③)
    episode_id = await episodes.create(
        subject_type="strategy",
        subject_id=STRATEGY_ID,
        title="Keltner V2 @ SOL/USDT 4h 实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
        recorded={
            "entry_price": 148.20,
            "exit_price": 137.90,
            "pnl_pct": -0.0695,
            "declared": {"symbol": "BTC/USDT", "interval": "1h"},
            "as_run": {"symbol": "SOL/USDT", "interval": "4h"},
            "backtest_id": BACKTEST_ID,
        },
    )
    await edges.add_fact_edge(
        from_ref=("episode", episode_id),
        to_ref=("question", question_id),
        kind="addresses",
    )

    # **当时只知道这一个数字。** 重算和事后归因是后来才发生的观察,
    # 由 round2 产生 —— 这正是 §8③"事件没变,观察在变"。
    recorded_ev = await evidence.create(
        episode_id=episode_id,
        mode="RECORDED",
        payload={"pnl_pct": -0.0695, "note": "实跑记账的当时数字"},
        source_ref=BACKTEST_ID,
    )
    await edges.add_fact_edge(
        from_ref=("evidence", recorded_ev),
        to_ref=("question", question_id),
        kind="addresses",
    )

    drift_hyp = await hypotheses.create(DRIFT_STATEMENT)
    regime_hyp = await hypotheses.create(REGIME_STATEMENT)
    for hyp_id in (drift_hyp, regime_hyp):
        await edges.add_fact_edge(
            from_ref=("hypothesis", hyp_id),
            to_ref=("question", question_id),
            kind="addresses",
        )

    # 判断边必须带推理依据 + 出处(D-3)
    await edges.add_judgment_edge(
        from_ref=("evidence", recorded_ev),
        to_ref=("hypothesis", drift_hyp),
        kind="supports",
        rationale="declared 与 as_run 的 symbol/interval 不一致,与漂移假设方向一致",
        provenance=[BACKTEST_ID],
        actor="selene",
    )
    await edges.add_judgment_edge(
        from_ref=("evidence", recorded_ev),
        to_ref=("hypothesis", regime_hyp),
        kind="qualifies",
        rationale="当时的记录只到 8 月底,无法区分 regime 因素",
        provenance=[BACKTEST_ID],
        actor="selene",
    )

    experiment_id = await experiments.create(
        kind="rerun_with_declared_config",
        hypothesis_id=drift_hyp,
        config={"symbol": "BTC/USDT", "interval": "1h", "strategy_id": STRATEGY_ID},
    )
    await edges.add_fact_edge(
        from_ref=("experiment", experiment_id),
        to_ref=("question", question_id),
        kind="addresses",
    )
    await experiments.mark_ran(experiment_id, ran_at="2026-09-16T02:00:00+00:00")

    revision = await questions.add_revision(
        question_id,
        ROUND1_ANSWER,
        actor="selene",
        rationale=["declared 与 as_run 不一致", "当时记录的 pnl 为负"],
        cited=[recorded_ev, drift_hyp],
    )

    return {
        "question_id": question_id,
        "episode_id": episode_id,
        "evidence_id": recorded_ev,
        "revision": revision,
    }


# ---------------------------------------------------------------------------
# round2 —— 现实开过口之后(输入只有一个 db 句柄)
# ---------------------------------------------------------------------------


async def run_round2(db: Any, *, recall_enabled: bool = True) -> dict[str, Any]:
    """第二版回答。**输入只有一个 db 句柄。**

    问题在哪、第一版说了什么、有哪些证据 —— 全部自己从磁盘上找回来。
    这就是"过一段真实时间"的可证伪替身:**承载证据价值的是进程隔离,
    不是日历天数。**
    """
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)
    episodes = EpisodeRepository(db)
    evidence = EvidenceRepository(db)
    decisions = DecisionRepository(db)

    question_id = await find_question_id(db, QUESTION_TITLE)
    if question_id is None:
        raise SystemExit("库里没有这个问题 —— round1 还没跑过")

    # 现实的两次发言(后来才发生的观察,§8③)
    # 走仓储而不是原生 SQL —— round2 要证明的正是"只靠领域接口就能从磁盘上
    # 把自己接回来";绕过仓储去查表会让这个证明掺水。
    found = await episodes.list_episodes(
        subject_type="strategy", subject_id=STRATEGY_ID, limit=1
    )
    if not found:
        raise SystemExit("库里没有这条 Episode —— round1 没跑完整")
    episode_id = found[0]["id"]

    reconstructed_ev = await evidence.create(
        episode_id=episode_id,
        mode="RECONSTRUCTED",
        payload={"pnl_pct": -0.115, "engine": "tick-recompute-v1"},
        source_ref="tick-recompute-2026-09-16",
    )
    after_fact_ev = await evidence.create(
        episode_id=episode_id,
        mode="AFTER_THE_FACT",
        payload={"regime": "post_shift", "note": "该段位于 regime shift 后半程"},
        source_ref="regime-classifier-v2",
    )
    for ev_id in (reconstructed_ev, after_fact_ev):
        await edges.add_fact_edge(
            from_ref=("evidence", ev_id),
            to_ref=("question", question_id),
            kind="addresses",
        )

    drift_hyp = await _hypothesis_by_statement(db, DRIFT_STATEMENT)
    regime_hyp = await _hypothesis_by_statement(db, REGIME_STATEMENT)
    if drift_hyp is None or regime_hyp is None:
        raise SystemExit("库里没有两条竞争假设 —— round1 没跑完整")

    await edges.add_judgment_edge(
        from_ref=("evidence", reconstructed_ev),
        to_ref=("hypothesis", drift_hyp),
        kind="qualifies",
        rationale="按声明配置重跑后 OOS 仍为负 —— 漂移是必要不充分条件",
        provenance=["tick-recompute-2026-09-16"],
        actor="selene",
    )
    await edges.add_judgment_edge(
        from_ref=("evidence", after_fact_ev),
        to_ref=("hypothesis", regime_hyp),
        kind="supports",
        rationale="该段处于 regime shift 后半程,与 regime 假设方向一致",
        provenance=["regime-classifier-v2"],
        actor="selene",
    )

    # ---- 分界线:引用从哪来 -------------------------------------------------
    recalled: list[dict[str, Any]] = []
    if recall_enabled:
        recalled = await recall(
            db, question_id=question_id, subject=("strategy", STRATEGY_ID)
        )
    cited = [c["id"] for c in recalled]

    prior = next((c for c in recalled if c["kind"] == "revision"), None)
    if prior is not None:
        content = (
            ROUND2_WITH_PRIOR_PREFIX
            + (prior["text"] or "")[:40]
            + ROUND2_WITH_PRIOR_SUFFIX
        )
        rationale = [
            "重算显示实际亏损更大",
            "事后 regime 归因改变了权重",
            f"对照第一版({prior['id']}):该判断被限定而非推翻",
        ]
    else:
        content = ROUND2_BLIND
        rationale = [
            "重算显示实际亏损更大",
            "事后 regime 归因改变了权重",
            "未取回此前研究历史 —— 本版不含与既有判断的对照",
        ]

    revision = await questions.add_revision(
        question_id, content, actor="selene", rationale=rationale, cited=cited
    )

    decision_id = await decisions.record(
        question_id,
        "REVALIDATE_WITH_REGIME_PRECONDITION",
        rationale="漂移假设被限定,regime 假设被支持 —— 按修正后的前置条件重验证",
        window_start="2026-09-17T00:00:00+00:00",
        window_end="2026-10-17T00:00:00+00:00",
    )

    return {
        "question_id": question_id,
        "revision": revision,
        "citation_migration": await questions.citation_migration(question_id),
        "recall": {
            "enabled": recall_enabled,
            "count": len(recalled),
            "ids": cited,
            "classes": sorted({c["recall_class"] for c in recalled}),
        },
        "decision_id": decision_id,
    }


async def _hypothesis_by_statement(db: Any, statement: str) -> str | None:
    rows = await db.execute(
        "SELECT id FROM research_hypotheses WHERE statement = ? LIMIT 1", (statement,)
    )
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------------------
# 库连接
# ---------------------------------------------------------------------------


def default_db_path() -> Path:
    return Path.home() / ".selenespace" / "data" / "selenespace.db"


async def _with_db(db_path: Path, coro_factory: Any) -> Any:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(str(db_path), migration_runner=runner) as db:
        return await coro_factory(db)


# ---------------------------------------------------------------------------
# acceptance —— 两进程 × 两组,全自动
# ---------------------------------------------------------------------------


def _spawn(args: list[str], db_path: Path) -> None:
    """**真子进程。** 这是本脚本最强的一条证据:round2 跑在一个空进程里。"""
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args, "--db", str(db_path)],
        check=True,
    )


def _clip(text: str, width: int = 40) -> str:
    return text if len(text) <= width else text[:width] + "…"


async def _describe_cited(db: Any, node_id: str) -> str:
    """把一条被引用的对象翻成一句人话。**返回值里不含 id** —— 这是刻意的:
    人话 diff 那一节要让读者的眼睛落在"为什么不同"上,不是落在 id 上。"""
    if node_id.startswith("rev-"):
        rows = await db.execute(
            "SELECT content FROM research_revisions WHERE id = ?", (node_id,)
        )
        if not rows:
            return "一条已经找不到的回答"
        return f"第一版回答 ——「{_clip(rows[0]['content'])}」"
    if node_id.startswith("hyp-"):
        rows = await db.execute(
            "SELECT statement FROM research_hypotheses WHERE id = ?", (node_id,)
        )
        if not rows:
            return "一条已经找不到的假设"
        return f"当时提出的假设 ——「{rows[0]['statement']}」"
    return "一类当时留下的其它成果"


async def _read_arm(db_path: Path) -> dict[str, Any]:
    async def _read(db: Any) -> dict[str, Any]:
        question_id = await find_question_id(db, QUESTION_TITLE)
        if question_id is None:
            raise SystemExit(f"{db_path} 里没有这个问题")
        revisions = await ResearchQuestionRepository(db).list_revisions(question_id)
        first, second = revisions[0], revisions[-1]
        integrity = await check_graph_integrity(db)
        return {
            "question_id": question_id,
            "round1_revision_id": first["id"],
            "round2_revision_id": second["id"],
            "round1_content": first["content"],
            "round2_cited": list(second["cited"]),
            # 逐条翻成人话 —— 人话 diff 要的正是这个,**不是**上面的 id 列表
            "round2_cited_human": [
                await _describe_cited(db, node_id) for node_id in second["cited"]
            ],
            "round2_content": second["content"],
            "integrity": {
                "ok": integrity["ok"],
                "violations": integrity["violations"],
                "counts": integrity["counts"],
            },
        }

    # LHS 注解是为了过 mypy 的 no-any-return:_with_db 返回 Any。
    result: dict[str, Any] = await _with_db(db_path, _read)
    return result


# ---------------------------------------------------------------------------
# 人话 diff —— W1-B 的真正出口
# ---------------------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    """切成句。只按中文句读与换行切 —— 这是给人读的,不做 tokenize。"""
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。!?;\n":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _missing_sentences(armed: str, blind: str) -> list[str]:
    """有召回那版里有、禁用召回那版里没有的句子 —— **指着两段原文说**。

    判"在不在"用**整段子串包含**,不是句子集合成员:两版共有的同一句可能
    因为前面挂着的小标题不同(「修正判断:」黏在哪一句上)而被切成不同的
    句子,用集合判定会把它误报成"少了"。误报一次,这一节的信任就没了。
    """
    return [s for s in _sentences(armed) if s not in blind]


def _render_diff(a: dict[str, Any], b: dict[str, Any]) -> None:
    """人话 diff —— 把"第二版为什么与第一版不同"摊开给人看。

    **纯打印,不碰 DB;不打印任何 id / JSON / 代码。** 计划原来的出口是两条
    截断到 80 字的回答加一个 cited 列表 —— 那不是人话 diff:读者的眼睛会落在
    id 上,而不是落在"第二版为什么不同"上。所以这一节打印**全文**。

    这就是 W1-B 的验收出口:读完这一节,要能**一句话**说出第二版因为取回了
    什么而不同。说不出 → 改的是 ROUND2_* 三个常量的散文内容,不是判据。
    """
    print()
    print("=" * 72)
    print("人话 diff —— 第二版为什么与第一版不同")
    print("=" * 72)

    print()
    print("【第一版回答(全文)】")
    print(a["round1_content"])

    print()
    print("【第二版回答(全文,有召回)】")
    print(a["round2_content"])

    print()
    print("【本版取回了】")
    if a["round2_cited_human"]:
        for line in a["round2_cited_human"]:
            print(f"  · {line}")
    else:
        print("  (什么都没取回)")

    print()
    print("【关掉召回后少了】(对照版第二版回答的全文在下面,逐句指认)")
    missing = _missing_sentences(a["round2_content"], b["round2_content"])
    if missing:
        for line in missing:
            print(f"  · {line}")
    else:
        print("  (两版逐句相同 —— 召回没有改变回答,这正是 Memory 退化成档案柜的样子)")

    print()
    print("【对照版第二版回答(全文,禁用召回)】")
    print(b["round2_content"])

    print()
    print("一句话:第二版因为取回了上面【本版取回了】那几项,才说出第一版没有的")
    print("那句话;而禁用召回的对照版说不出它 —— 差异只能归因给召回。")


async def _cmd_acceptance() -> None:
    """§11.4 的完整证据:两进程 × (有召回 / 无召回),然后比对。

    判据有两半,合起来才算数:
    1. ``packages/storage/tests/test_keltner_research_cycle.py`` 的**四个测试**
       —— 进程隔离 / 引用来自 recall / 对照组 / 跑完图仍干净。
    2. 这里 —— 真的开子进程跑一遍,打印 gate PASS、2A 与 2B 的差异、
       §9.5 的图完整性,最后给人话 diff。

    出口是 ``_render_diff`` 那一节 —— 机器可验的部分(下面的 acceptance
    字典)只是它的支撑。
    """
    workdir = Path(tempfile.mkdtemp(prefix="keltner-acceptance-"))
    arm_a = workdir / "arm-2a.db"
    arm_b = workdir / "arm-2b.db"
    print(f"[acceptance] workdir: {workdir}")

    for label, db_path, extra in (
        ("2A(有召回)", arm_a, []),
        ("2B(禁用召回)", arm_b, ["--no-recall"]),
    ):
        print(f"[acceptance] {label}: round1 ...")
        _spawn(["round1"], db_path)  # ← 独立进程,写完即退出
        print(f"[acceptance] {label}: round2 ...")
        _spawn(["round2", *extra], db_path)  # ← 另一个独立进程

    a = await _read_arm(arm_a)
    b = await _read_arm(arm_b)

    acceptance = {
        # 进程隔离:两轮确实跑在两个进程里
        "two_processes": True,
        # 2A 引用了第一轮留下的东西
        "round2a_cites_round1": a["round1_revision_id"] in a["round2_cited"],
        # 2B 没有召回,所以它无从得知该引谁
        "round2b_is_blind": b["round2_cited"] == [],
        # 对照组的差异 —— **可归因给 recall 的那部分**
        "arms_differ_in_citation": set(a["round2_cited"]) != set(b["round2_cited"]),
        "arms_differ_in_content": a["round2_content"] != b["round2_content"],
    }
    acceptance["attributable_to_recall"] = (
        acceptance["round2a_cites_round1"]
        and acceptance["round2b_is_blind"]
        and acceptance["arms_differ_in_citation"]
        and acceptance["arms_differ_in_content"]
    )
    acceptance["passed"] = acceptance["attributable_to_recall"]

    print()
    print(f"2A round1 revision : {a['round1_revision_id']}")
    print(f"2A round2 cited    : {a['round2_cited']}")
    print(f"2B round2 cited    : {b['round2_cited']}")
    print(f"2A integrity       : {a['integrity']}")
    print(f"acceptance         : {acceptance}")

    if not acceptance["passed"]:
        raise SystemExit(
            "§11 gate 未通过 —— 第二次回答与第一次的差异无法归因给 recall。"
            "这是**实现没做完**,不是定位被证伪(§11.3)。"
        )
    if not a["integrity"]["ok"]:
        raise SystemExit(
            f"§9.5 图不干净 —— 循环跑出了违规:{a['integrity']['violations']}"
        )
    print()
    print("§11 gate PASS:第二次回答因为取回了第一次研究留下的东西而不同。")

    print()
    print(LIMITS)
    _render_diff(a, b)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Keltner 研究循环")
    parser.add_argument("phase", choices=["round1", "round2", "acceptance"])
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--no-recall",
        action="store_true",
        help="对照组 2B:禁用召回,只开库文件",
    )
    args = parser.parse_args()

    if args.phase == "acceptance":
        asyncio.run(_cmd_acceptance())
        return

    db_path = args.db or default_db_path()
    print(f"[keltner-research-cycle] phase={args.phase} db={db_path}")

    if args.phase == "round1":
        result = asyncio.run(_with_db(db_path, run_round1))
        print(f"question : {result['question_id']}")
        print(f"episode  : {result['episode_id']}")
        print(f"evidence : {result['evidence_id']} (RECORDED)")
        print(f"revision : {result['revision']['id']} seq={result['revision']['seq']}")
        print("round1 完成 —— 进程到此退出。")
        return

    result = asyncio.run(
        _with_db(db_path, lambda db: run_round2(db, recall_enabled=not args.no_recall))
    )
    print(f"question : {result['question_id']}")
    print(f"recall   : {result['recall']}")
    print(f"revision : {result['revision']['id']} seq={result['revision']['seq']}")
    print(f"cited    : {result['revision']['cited']}")
    print(f"migration: {result['citation_migration']}")
    print(f"decision : {result['decision_id']}")


if __name__ == "__main__":
    main()
