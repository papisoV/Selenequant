"""ledger_bridge roundtrip:公式注册落账 / 重测成链 / 取最新语义。

六个验收(计划 v3 文件清单 #5):
- roundtrip: register → load_known_formulas 取回同一条
- 重测成链(改1): 同 hash 二次 register 产生**第二** episode(append-only,
  不幂等压平),derived_from 子→父成链,load 取到第二次
- digest 过滤: 最新条目 vocab_digest 不符 ⇒ 不拦(W7 双键身份歧义)
- 空账本: 库文件不存在 ⇒ 返回 {} 且不建库(读路径零副作用)
- 脏数据跳过: recorded_json 不可解析的行不参选最新,同 subject 更早的
  有效条目仍可确立身份
- lifecycle 初始 DISCOVERED(先例 run_probe/factor_snapshot:落账不做
  认识论判断,无 lifecycle 子键的 episode 不可流转)

运行模式:root 模式(root pyproject pythonpath 含 packages/storage);
本文件自带 storage path 兜底注入(照 test_no_torch_in_search_import 的
文件内 _PKG_DIRS 先例),conftest 不动(计划 改3)。bridge 的公开 API 是
同步函数(内部 asyncio.run),测试零新依赖,不需要 pytest-asyncio。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _pkg in ("packages/storage", "packages/factor-research", "packages/alpha-core"):
    _p = str(_REPO_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from selene_space_factor_research.mining.ledger_bridge import (  # noqa: E402
    register_formula_episode,
    load_known_formulas,
)

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-02T00:00:00+00:00"
DIGEST_A = "aaaa1111"
DIGEST_B = "bbbb2222"
TOKENS = [3, 7]
HASH = "deadbeef01"


def _register(
    db, *, hash_=HASH, digest=DIGEST_A, at=None, verdict="VALID", tokens=None
) -> dict:
    return register_formula_episode(
        db,
        canonical_hash=hash_,
        vocab_digest=digest,
        formula_tokens=tokens if tokens is not None else TOKENS,
        readable="RET TS_RANK",
        source="test",
        verdict=verdict,
        occurred_at=at,
    )


def _rows(db: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def test_roundtrip_register_then_load(tmp_path) -> None:
    db = tmp_path / "ledger.db"
    out = _register(db)

    assert out["new_episode_id"].startswith("ep-")
    assert out["latest"]["canonical_hash"] == HASH
    assert out["latest"]["vocab_digest"] == DIGEST_A
    assert out["latest"]["formula_tokens"] == TOKENS

    known = load_known_formulas(db)
    assert HASH in known
    assert known[HASH]["episode_id"] == out["new_episode_id"]
    assert known[HASH]["vocab_digest"] == DIGEST_A


def test_retest_appends_and_chains_derived_from(tmp_path) -> None:
    db = tmp_path / "ledger.db"
    first = _register(db, at=T0, verdict="VALID")
    second = _register(db, at=T1, verdict="SUSPICIOUS")

    # append-only:两次 register = 两个 episode,不幂等压平(verdict 变化
    # 是 IC 衰减证据,必须留痕)
    assert second["new_episode_id"] != first["new_episode_id"]
    episodes = _rows(
        db,
        "SELECT id FROM research_episodes "
        "WHERE subject_type = 'formula' AND subject_id = 'formula:" + HASH + "'",
    )
    assert len(episodes) == 2

    # derived_from 子→父成链
    edges = _rows(
        db,
        "SELECT from_id, to_id FROM research_edges "
        "WHERE kind = 'derived_from' AND from_type = 'episode' "
        "AND to_type = 'episode'",
    )
    assert edges == [
        {"from_id": second["new_episode_id"], "to_id": first["new_episode_id"]}
    ]

    # load 取到第二次(最新):verdict SUSPICIOUS
    known = load_known_formulas(db)
    assert known[HASH]["episode_id"] == second["new_episode_id"]


def test_digest_mismatch_is_filtered_out(tmp_path) -> None:
    db = tmp_path / "ledger.db"
    _register(db, digest=DIGEST_A)

    # 同 digest → 拦
    assert HASH in load_known_formulas(db, vocab_digest=DIGEST_A)
    # digest 不符 → 身份歧义,不拦
    assert HASH not in load_known_formulas(db, vocab_digest=DIGEST_B)
    # 不过滤(vocab_digest=None)→ 返回全部
    assert HASH in load_known_formulas(db, vocab_digest=None)


def test_missing_db_returns_empty_without_creating_file(tmp_path) -> None:
    db = tmp_path / "never-created.db"
    assert load_known_formulas(db) == {}
    assert not db.exists(), "读路径零副作用:不得建库"


def test_dirty_recorded_rows_are_skipped_for_latest(tmp_path) -> None:
    db = tmp_path / "ledger.db"
    valid = _register(db, at=T0)

    # 直接插脏行(同 subject、occurred_at 更晚、recorded_json 不可解析)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, recorded_json, occurred_at, created_at) "
        "VALUES ('ep-dirty', 'formula', ?, 'dirty', ?, ?, '{not-json', ?, ?)",
        (f"formula:{HASH}", T1, T1, T1, T1),
    )
    # 另一个 hash 只有脏行 → 不得出现在结果里
    conn.execute(
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, recorded_json, occurred_at, created_at) "
        "VALUES ('ep-dirty2', 'formula', 'formula:dirtyhash', 'dirty', "
        "?, ?, '{also-bad', ?, ?)",
        (T0, T0, T0, T0),
    )
    conn.commit()
    conn.close()

    known = load_known_formulas(db)
    # 脏行不参选:同 subject 取到更早的有效条目
    assert known[HASH]["episode_id"] == valid["new_episode_id"]
    assert "dirtyhash" not in known


def test_lifecycle_starts_at_discovered(tmp_path) -> None:
    db = tmp_path / "ledger.db"
    out = _register(db)

    rows = _rows(
        db,
        "SELECT recorded_json FROM research_episodes WHERE id = ?",
        (out["new_episode_id"],),
    )
    recorded = json.loads(rows[0]["recorded_json"])
    assert recorded["lifecycle"] == {"status": "DISCOVERED", "transitions": []}
    assert recorded["source"] == "test"
    assert recorded["vocab_digest"] == DIGEST_A
