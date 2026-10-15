"""W2 T8 / J3 走查 —— 机械证据一键脚本(用户完成双自然日走查后跑)。

验证计划 the implementation plan §T8 的判据:
  1. 用户走查的问题存在 seq=2 的回答,且其 cited 非空(第二次回答从账本引用了 ≥1 项)
  2. 第一版与第二版的创建时间跨 ≥2 个自然日(DISTINCT DAYS >= 2)

用法:python scripts/w2_j3_evidence.py [question_id]
  不带参数 = 列出所有有 ≥2 版回答的问题及其判据读数,逐个判定
"""

import sys
from pathlib import Path

import aiosqlite
import asyncio

DB_PATH = Path.home() / ".selenespace" / "data" / "selenespace.db"


async def main() -> int:
    qid_filter = sys.argv[1] if len(sys.argv) > 1 else None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if qid_filter:
            rows = await (
                await db.execute(
                    """SELECT question_id, COUNT(*) AS n,
                              MAX(seq) AS max_seq
                       FROM research_revisions WHERE question_id = ? GROUP BY question_id""",
                    (qid_filter,),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    """SELECT question_id, COUNT(*) AS n,
                              MAX(seq) AS max_seq
                       FROM research_revisions GROUP BY question_id HAVING COUNT(*) >= 2""",
                )
            ).fetchall()

        if not rows:
            print("(没有找到 >=2 版回答的问题)")
            return 3

        any_pass = False
        for r in rows:
            qid = r["question_id"]
            days = await (
                await db.execute(
                    """SELECT COUNT(DISTINCT substr(created_at, 1, 10)) AS d
                   FROM research_revisions WHERE question_id = ?""",
                    (qid,),
                )
            ).fetchone()
            # seq=2 那一版的 cited 是否非空
            v2 = await (
                await db.execute(
                    "SELECT id, cited_json FROM research_revisions WHERE question_id = ? AND seq = 2",
                    (qid,),
                )
            ).fetchone()
            v2_cited = v2["cited_json"] if v2 else None
            cited_nonempty = v2_cited not in (None, "", "[]")
            distinct_days = days["d"] if days else 0
            passed = cited_nonempty and distinct_days >= 2
            any_pass = any_pass or passed
            print(f"question={qid}")
            print(f"  max_seq          = {r['max_seq']}")
            print(
                f"  seq=2 cited      = {v2_cited!r}  -> {'非空 PASS' if cited_nonempty else '空 FAIL'}"
            )
            print(
                f"  distinct_days    = {distinct_days}  -> {'>=2 PASS' if distinct_days >= 2 else '<2 FAIL'}"
            )
            print(f"  判定             = {'PASS' if passed else 'FAIL'}")
            print()

        return 0 if any_pass else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
