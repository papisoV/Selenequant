#!/usr/bin/env python
"""Landing 快照刷新/校验 —— 让 real-data.ts 的计数块以真库为准。

背景(2026-10-04 审计发现): `lib/landing/real-data.ts` 是**静态 fixture**
(landing 需脱离 local-api 独立构建,故真值固化为常量)。但手写计数会**静默漂移**:
快照 2026-10-02 → 10-04 两天内 questions 40→47、decisions 0→2,而没有任何东西报警
(verify-round4.mjs 只做 DOM 断言、不连库)。

本脚本补上缺失的刷新管道:
  python scripts/refresh-landing-snapshot.py           # 校验,有漂移则非零退出
  python scripts/refresh-landing-snapshot.py --write   # 按真库写回计数块(不动叙述文字)

覆盖范围: **只动机械可导出的计数**(DB_OBJECTS.count / zero 标志 / SCALE_FACTS 数值)。
叙述文字(note 措辞、header 说明、各 section 的散文)是人工curated,脚本不碰,
但 --check 会把 questions 状态分布等一并打印,便于人工同步。

DB 路径默认 `~/.selenespace/data/selenespace.db`,可用 --db 或 $SELENE_DB 覆盖。
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

REAL_DATA = Path(__file__).resolve().parents[1] / "lib" / "landing" / "real-data.ts"

# DB_OBJECTS 里逐表登记的对象 —— (库表名, 无)
DB_TABLES = [
    "research_questions",
    "research_revisions",
    "research_evidence",
    "research_episodes",
    "research_missions",
    "research_edges",
    "research_decisions",
]

# SCALE_FACTS 里可从库直接 COUNT 的键 —— (常量键, 库表名)
SCALE_KEYS = {
    "strategies": "strategies",
    "backtests": "backtests",
    "questions": "research_questions",
    "evidence": "research_evidence",
}
# 不可从库导出、脚本保持原样的键(来源不可考,见 2026-10-04 审计)
SCALE_OPAQUE = ["interfaces"]


def default_db() -> Path:
    env = os.environ.get("SELENE_DB")
    if env:
        return Path(env)
    return Path.home() / ".selenespace" / "data" / "selenespace.db"


def live_counts(db_path: Path) -> dict[str, int]:
    """真库各表行数。表不存在 → 记 -1(而非崩),由调用方判为异常。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: dict[str, int] = {}
        for table in {*DB_TABLES, *SCALE_KEYS.values()}:
            try:
                out[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[
                    0
                ]
            except sqlite3.Error:
                out[table] = -1
        return out
    finally:
        conn.close()


def parse_current(text: str) -> tuple[dict[str, int], dict[str, int]]:
    """从 real-data.ts 解析当前登记的计数。"""
    db_obj: dict[str, int] = {}
    for table in DB_TABLES:
        m = re.search(r'\{ table: "%s".*?count:\s*(\d+)' % re.escape(table), text, re.S)
        if m:
            db_obj[table] = int(m.group(1))

    scale: dict[str, int] = {}
    m = re.search(r"export const SCALE_FACTS = \{(.*?)\} as const;", text, re.S)
    if m:
        for key in SCALE_KEYS:
            mm = re.search(r"%s:\s*(\d+)" % re.escape(key), m.group(1))
            if mm:
                scale[key] = int(mm.group(1))
    return db_obj, scale


def apply_counts(text: str, live: dict[str, int]) -> str:
    """把真库计数写回 DB_OBJECTS.count / zero 标志 + SCALE_FACTS 数值。"""
    # DB_OBJECTS.count
    for table in DB_TABLES:
        n = live.get(table, -1)
        if n < 0:
            continue
        text = re.sub(
            r'(\{ table: "%s".*?count:\s*)\d+' % re.escape(table),
            lambda m, n=n: m.group(1) + str(n),
            text,
            flags=re.S,
        )

    # zero 标志:count==0 必须带 zero:true,否则必须去掉(两者互斥,驱动 ZERO/NONZERO 分组)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.search(r'table: "([^"]+)"', line)
        if not m or m.group(1) not in DB_TABLES:
            continue
        should_zero = live.get(m.group(1), -1) == 0
        has_zero = "zero: true" in line
        if should_zero and not has_zero:
            lines[i] = line.replace(" }", ", zero: true }")
        elif not should_zero and has_zero:
            lines[i] = line.replace(", zero: true", "")
    text = "\n".join(lines)

    # SCALE_FACTS
    def _scale_sub(block_text: str) -> str:
        for key, table in SCALE_KEYS.items():
            n = live.get(table, -1)
            if n < 0:
                continue
            block_text = re.sub(
                r"(%s:\s*)\d+" % re.escape(key),
                lambda m, n=n: m.group(1) + str(n),
                block_text,
            )
        return block_text

    text = re.sub(
        r"(export const SCALE_FACTS = \{)(.*?)(\} as const;)",
        lambda m: m.group(1) + _scale_sub(m.group(2)) + m.group(3),
        text,
        flags=re.S,
    )
    return text


def stamp_date(text: str, date_str: str) -> str:
    """刷新 header 的 SNAPSHOT 戳(若不存在则插入)。行尾安全(不吞 CRLF 的 \\r)。"""
    line = f" * SNAPSHOT: {date_str} (refresh-landing-snapshot.py --write 自动戳)"
    # [^\r\n]* 而非 .*$ —— 后者会把 CRLF 的 \r 一并吃掉,产出混合行尾
    if re.search(r"^ \* SNAPSHOT:[^\r\n]*", text, re.M):
        return re.sub(r"^ \* SNAPSHOT:[^\r\n]*", line, text, count=1, flags=re.M)
    # 插到 header 第一行 `/**` 之后,沿用该行行尾
    m = re.search(r"^/\*\*([^\r\n]*)(\r?\n)", text, re.M)
    if not m:
        return text
    return text[: m.end()] + line + m.group(2) + text[m.end() :]


def report(real: Path, live: dict[str, int], cur_db: dict, cur_scale: dict) -> bool:
    """打印对照表,返回 True=无漂移。"""
    drift: list[str] = []
    print(f"=== {real.name} 快照 vs 真库 ===")
    print(f"{'项':<28}{'快照':>8}{'真库':>8}   状态")
    for table in DB_TABLES:
        snap, live_n = cur_db.get(table), live.get(table, -1)
        mark = "OK" if snap == live_n else "DRIFT"
        if snap != live_n:
            drift.append(f"{table}: {snap} → {live_n}")
        print(f"{'DB.' + table:<28}{str(snap):>8}{str(live_n):>8}   {mark}")
    for key, table in SCALE_KEYS.items():
        snap, live_n = cur_scale.get(key), live.get(table, -1)
        mark = "OK" if snap == live_n else "DRIFT"
        if snap != live_n:
            drift.append(f"SCALE_FACTS.{key}: {snap} → {live_n}")
        print(f"{'SCALE.' + key:<28}{str(snap):>8}{str(live_n):>8}   {mark}")
    for key in SCALE_OPAQUE:
        print(f"{'SCALE.' + key:<28}{str(cur_scale.get(key)):>8}{'—':>8}   人工维护")
    print()
    if drift:
        print("漂移明细:")
        for d in drift:
            print(f"  - {d}")
        print("\n运行 `--write` 写回机械计数;叙述文字(note/散文)请人工同步。")
        return False
    print("无漂移。")
    return True


def print_advisory(db_path: Path) -> None:
    """把 note 里嵌了数字的分布一并打印,便于人工同步散文。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        print("=== 供人工同步的分布(note 里嵌数字的项) ===")
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM research_questions GROUP BY status ORDER BY 2 DESC"
        ).fetchall()
        print("  research_questions 状态: " + " / ".join(f"{s}={n}" for s, n in rows))
        rev = conn.execute("SELECT cited_json FROM research_revisions").fetchall()
        with_cite = sum(1 for (j,) in rev if j and j.strip() not in ("", "[]", "null"))
        print(f"  research_revisions 带引用边: {with_cite} / {len(rev)}")
        dec = conn.execute(
            "SELECT outcome_status, COUNT(*) FROM research_decisions GROUP BY outcome_status"
        ).fetchall()
        if dec:
            print(
                "  research_decisions 状态: " + " / ".join(f"{s}={n}" for s, n in dec)
            )
        print()
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--write", action="store_true", help="按真库写回计数块(默认只校验)")
    ap.add_argument("--db", type=Path, default=None, help="selenespace.db 路径")
    ap.add_argument(
        "--date", default=None, help="写回时的 SNAPSHOT 日期(默认取系统日期)"
    )
    args = ap.parse_args()

    db_path = args.db or default_db()
    if not db_path.exists():
        print(f"[ERR] 库不存在: {db_path}", file=sys.stderr)
        return 2

    live = live_counts(db_path)
    # 保留文件原换行符(LF/CRLF),避免整文件 diff
    raw = REAL_DATA.read_bytes().decode("utf-8")
    cur_db, cur_scale = parse_current(raw)

    if not args.write:
        ok = report(REAL_DATA, live, cur_db, cur_scale)
        print_advisory(db_path)
        return 0 if ok else 1

    from datetime import date as _date

    updated = apply_counts(raw, live)
    updated = stamp_date(updated, args.date or _date.today().isoformat())
    if updated == raw:
        print("已是最新,无需写回。")
        return 0
    REAL_DATA.write_bytes(updated.encode("utf-8"))
    print(f"[OK] 已写回 {REAL_DATA}")
    # 写回后自检
    cur_db2, cur_scale2 = parse_current(updated)
    ok = report(REAL_DATA, live, cur_db2, cur_scale2)
    print_advisory(db_path)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
