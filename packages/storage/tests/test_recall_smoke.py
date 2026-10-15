"""最小召回回路 —— 两进程 + 对照组(路线图 W1-A / Task 5C)。

这个文件存在的唯一理由是**跨进程**。同进程里"我记得"可以靠变量,跨进程只能
靠库;所以这里所有断言都建立在两件事上:round2 是一个真正的**新进程**,而它
拿到的输入只有一个 db 文件路径。

三段论:
① 进程隔离成立 —— round1 写下的 revision id 只活在数据库文件里
② 分层成立 —— 两类候选都被取回,且**分得清**身份(§6.10 ⑤)
③ 差异可归因 —— 关掉 recall 后账本为空,且两进程输入一字不差
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "recall_smoke.py"
DB_NAME = "w1a.db"
CONTROL_FLAG = "--no-recall"
T0 = "2026-09-01T00:00:00+00:00"

_WRITTEN = re.compile(r"^写入: (\S+)", re.M)
_ENTRY = re.compile(r"^  \[(learned_experience|research_record)\] (\S+)", re.M)


def _child_env() -> dict[str, str]:
    # 子进程往管道写中文,Windows 默认按 locale(GBK)编码 —— 钉成 UTF-8,
    # 否则这条断言测的是编码而不是召回。
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,  # 物理上不存在 stdin 这条通道
        env=env,
        timeout=180,
    )


def _entries(stdout: str) -> dict[str, str]:
    return {m.group(2): m.group(1) for m in _ENTRY.finditer(stdout)}


def _db_file_names(db: Path) -> set[str]:
    """数据库**本身**。WAL / SHM 是同一个库的组成部分,不是一个旁路通道。"""
    return {db.name, f"{db.name}-wal", f"{db.name}-shm"}


def test_recall_survives_process_boundary(tmp_path):
    db = tmp_path / DB_NAME
    env = _child_env()

    # --- 进程 A:写库后退出 ------------------------------------------------
    first = _run(["round1", "--db", str(db)], env)
    assert first.returncode == 0, first.stderr
    written = _WRITTEN.findall(first.stdout)
    assert len(written) == 2, f"round1 必须同时落两类候选:\n{first.stdout}"
    revision_id = next(i for i in written if i.startswith("rev-"))
    hypothesis_id = next(i for i in written if i.startswith("hyp-"))

    # --- ① round1 写下的 id 只活在数据库文件里 ----------------------------
    carriers = sorted(
        p.name
        for p in tmp_path.iterdir()
        if p.is_file() and revision_id.encode("utf-8") in p.read_bytes()
    )
    assert carriers, "库里必须真的有这个 id —— 否则下面所有断言都是空转"
    assert set(carriers) <= _db_file_names(
        db
    ), f"这个 id 只能活在数据库文件里,实际还出现在 {carriers}"

    # --- 进程 B:另一个进程,只拿到一个 db 文件路径 ------------------------
    second = _run(["round2", "--db", str(db)], env)
    assert second.returncode == 0, second.stderr
    assert revision_id not in " ".join(second.args), "argv 不是传递通道"
    assert all(revision_id not in v for v in env.values()), "env 不是传递通道"

    # --- ② 取回了,而且分得清 ---------------------------------------------
    ledger = _entries(second.stdout)
    assert ledger.get(revision_id) == "learned_experience", second.stdout
    assert ledger.get(hypothesis_id) == "research_record", second.stdout
    assert set(ledger.values()) == {
        "learned_experience",
        "research_record",
    }, "两类必须各出现至少一条 —— 只跑到 revision 路径就没覆盖 §6.10 分层"

    # --- ③ 差异只归因到"取回了历史"这一件事 -------------------------------
    third = _run(["round2", "--db", str(db), CONTROL_FLAG], env)
    assert third.returncode == 0, third.stderr
    assert third.args == [
        *second.args,
        CONTROL_FLAG,
    ], "对照组与默认运行的输入必须一字不差,只多一个开关"
    assert _entries(third.stdout) == {}, f"对照组账本必须为空:\n{third.stdout}"
    assert revision_id not in third.stdout
    assert "本次未取回历史" in third.stdout


# ---------------------------------------------------------------------------
# symbols 键回归(刀2 附带):market_material 给问题 anchor 加了 "symbols"
# 键 —— 召回的 anchor 匹配只认三标准键(strategy_ids/intervals/regimes),
# symbols 不参与匹配。两条断言锁死这个行为:含 symbols 不影响标准键命中,
# 只有 symbols 时不命中(否则 market_snapshot evidence 会跨问题漏召回)。
# ---------------------------------------------------------------------------


def test_anchor_with_symbols_key_does_not_change_standard_matching(tmp_path):
    import asyncio
    import json

    from selene_space_storage.migrations import MigrationRunner
    from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
    from selene_space_storage.repositories.research_graph.recall import recall
    from selene_space_storage.sqlite import AsyncSQLiteManager

    async def scenario(anchor: dict) -> list[str]:
        runner = MigrationRunner(ALL_MIGRATIONS)
        async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
            await db.execute(
                "INSERT INTO research_questions (id, title, body, anchor_json, "
                "status, opened_at, updated_at) VALUES "
                "('q-owner', '锚问题', '', ?, 'open', ?, ?)",
                (json.dumps({"strategy_ids": ["strat-x"]}), T0, T0),
            )
            await db.execute(
                "INSERT INTO research_hypotheses (id, statement, status, "
                "created_at, updated_at) VALUES "
                "('hyp-sym', '锚定假设', 'proposed', ?, ?)",
                (T0, T0),
            )
            await db.execute(
                "INSERT INTO research_edges (id, from_type, from_id, to_type, "
                "to_id, kind, edge_class, actor, created_at) VALUES "
                "('e-sym', 'hypothesis', 'hyp-sym', 'question', 'q-owner', "
                "'addresses', 'fact', 'system', ?)",
                (T0,),
            )
            await db.execute(
                "INSERT INTO research_questions (id, title, body, anchor_json, "
                "status, opened_at, updated_at) VALUES "
                "('q-probe', '探测问题', '', ?, 'open', ?, ?)",
                (json.dumps(anchor), T0, T0),
            )
            out = await recall(db, question_id="q-probe", kinds=("hypothesis",))
            return [c["id"] for c in out]

    # 标准键重叠 + 夹带 symbols → 照常命中(symbols 不添乱)
    hit = asyncio.run(
        scenario({"strategy_ids": ["strat-x"], "intervals": ["1d"], "symbols": ["BTC"]})
    )
    assert hit == ["hyp-sym"], f"standard keys + symbols broke matching: {hit}"

    # 只有 symbols → 零命中(symbols 不参与匹配)
    miss = asyncio.run(scenario({"symbols": ["BTC"]}))
    assert miss == [], f"symbols-only anchor unexpectedly matched: {miss}"
