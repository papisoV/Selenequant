#!/usr/bin/env python3
r"""audit_color_tokens.py — 语义色 token 三层审计(Step 0,零代码改动,只盘点不迁移)

口径三要素(先读这个再信任何数字):
  1. 计数口径 = 匹配行数(一行内命中 N 次计 1;2026-10-03 与基线对账确认口径为行数非出现次数)
  2. 扫描范围 = apps/desktop/{components,lib,app} 递归
  3. 扩展名   = .ts .tsx .css;排除 *.test.* 文件与 node_modules 路径

三层查询(与基线口径逐字符一致):
  L1 TSX/TS utility 类  (text|bg|border)-(pos|neg)(-soft)?(?![\w-])     仅 .ts/.tsx
  L2 TSX/TS var 直读    var\(--color-(pos|neg)(-soft)?\)                仅 .ts/.tsx
  L3 CSS 规则体         var\(--color-(pos|neg|pos-soft|neg-soft)\)      仅 .css

数值自检硬门:
  - 三层计数各自 > 0;任一层为 0 → ERROR 退出。
    (空表 ≠ 没有错用——若外接 grep -E 之类 ERE 引擎,lookahead (?![\w-]) 不被支持
     会静默返回空表且退出码 0;本脚本全程 Python re,但仍保留该提示防误配。)
  - 总和须落在 444..844(基线 649 的 ±30%),出界报错退出。
对账:实际 vs 基线 498/25/126,单层偏差 > 10% 打 WARN(不退出)。

判据四级(死活,启发式初判,人工复核后生效):
  类型声明 ❌ → 标签映射 ❌ → 动态拼类名 ❌ → 赋值点 ✅
已知死规则 15 条(2026-10-03 行号;Step 1 改动 globals.css 后行号会漂移,须重测再信):
  rc-ink/rc-gold/rc-pos/rc-accent(:1544-1547) / .vhero-trigger .trig-value.pos/.neg(:306-307)
  .dp-market-dot.us/.cn(:466-467) + .dp-market-bar-fill.us/.cn(:473-474)
  .bt-run-btn(:1819) / .ot-submit,.ot-apply-suggest(:1829)
  .mk-tag.mk-spot(:1381)+.mk-stock_a(:1382)+.mk-stock_us(:1383)

用法:
  python scripts/audit_color_tokens.py                # 汇总打印 + 写审计表 TSV
  python scripts/audit_color_tokens.py --check        # 干跑:只打印,不写任何文件(幂等)
  python scripts/audit_color_tokens.py --self-check   # 内嵌阳性 fixture 自检,通过即退出

轴归属为启发式初判(关键词打标),带 ? 后缀表示待人工复核;死规则以行号表精确圈定。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---- 口径常量(改任何一处都会破坏与基线的对账,先改文档再改码) ----
SCAN_ROOTS = ("components", "lib", "app")
EXT_TS = {".ts", ".tsx"}
EXT_CSS = {".css"}
# 基线 = 当前预期值。三步迁移后的最终态:
#   L1 498→456 (Step3 B类 32 处 + Step2 backtests-page 6 处迁移出方向轴)
#   L2 25→22   (analysis-panel/serenity-panel 等 var 直读迁到 destructive)
#   L3 126→92  (Step1 CSS 层 34 行改向)
# 原快照(迁移前) = L1 498 / L2 25 / L3 126 = 649 —— 历史参照,勿据此判回归。
BASELINE_ORIG = {"L1": 498, "L2": 25, "L3": 126}
BASELINE = {"L1": 456, "L2": 22, "L3": 92}
HARD_MIN, HARD_MAX = 444, 844  # 总量级硬门(原 649 ±30%)

RE_L1 = re.compile(r"(text|bg|border)-(pos|neg)(-soft)?(?![\w-])")
RE_L2 = re.compile(r"var\(--color-(pos|neg)(-soft)?\)")
RE_L3 = re.compile(r"var\(--color-(pos|neg|pos-soft|neg-soft)\)")

# ---- 已知死规则(仅 globals.css;行号 1-based 闭区间) ----
# (行号区间, 族标签) — 标 dead 不迁移;行号对账 2026-10-03 Step 1 改动后实测。
# 注意:Step 1 之后再动 globals.css 行号会漂移,先 grep 上述规则名重测再更新本表。
DEAD_RULES: list[tuple[range, str]] = [
    (range(1542, 1546), "rc-*"),
    (range(303, 305), "trig-value"),
    (range(463, 465), "dp-market-dot"),
    (range(470, 472), "dp-market-bar-fill"),
    (range(1817, 1818), "bt-run-btn"),
    (range(1827, 1828), "ot-submit"),
    (range(1379, 1382), "mk-tag"),
]

REPORT_PATH = Path(__file__).resolve().parent / "audit_color_tokens_report.tsv"

# ---- 启发式关键词(轴归属;顺序即优先级) ----
AXIS_JUDGE_RED = re.compile(
    r"danger|\berr|error|crit|urgent|\bfail|invalid|blocked|\bbad\b|kill|回撤|止损|爆仓|告警|否决",
    re.IGNORECASE,
)
AXIS_JUDGE_GREEN = re.compile(
    r"\.(on|live|open|ok|valid|success)\b|\bdone\b|完成|在线", re.IGNORECASE
)
AXIS_DIR = re.compile(
    r"pnl|chg|profit|return|gain|loss|\bup\b|\bdown\b|buy|sell|\bask\b|\bbid\b|long|short|涨|跌|盈|亏|收益|涨幅",
    re.IGNORECASE,
)
AXIS_DECOR = re.compile(r"terminal|term-line|cursor", re.IGNORECASE)

# TS 侧判据四级启发式
TS_ASSIGN = re.compile(r"className\s*[=:]|style\s*=\s*\{")
TS_TEMPLATE = re.compile(r"`|\$\{")
TS_TYPE_DECL = re.compile(r"\binterface\b|\btype\s+\w+\s*=|\?:\s|\|\s*$|<\w+>\(")
TS_MAP_ENTRY = re.compile(r"^\s*['\w-]+\s*:\s*['\"`]")


def self_check() -> int:
    """内嵌阳性 fixture:先证工具能匹配,再信它的 0。"""
    fixture_tsx = r"""
// 阳性样本(字面,勿删):审计正则必须全部命中
const a = "text-pos";
const b = "text-neg";
const c = "bg-neg-soft";
const d = "var(--color-pos)";
const e = "var(--color-neg-soft)";
"""
    fixture_css = r"""
/* 阳性样本(字面,勿删) */
.x { color: var(--color-neg); }
"""
    l1 = len(RE_L1.findall(fixture_tsx))
    l2 = len(RE_L2.findall(fixture_tsx))
    l3 = len(RE_L3.findall(fixture_css))
    total = l1 + l2 + l3
    print(f"[self-check] fixture L1={l1} L2={l2} L3={l3} total={total}")
    ok = l1 >= 2 and l2 >= 2 and l3 >= 1 and total >= 3
    if not ok:
        print(
            "[self-check] FAIL: 正则未命中内嵌阳性样本——引擎不支持 lookahead 或正则被改坏,后续 0 计数不可信"
        )
        return 1
    # 反向:确认负样本不误报
    neg = r"text-pos-center bg-negatively var(--color-positive)"
    if RE_L1.search(neg) or RE_L2.search(neg):
        print("[self-check] FAIL: 负样本误报(lookahead 边界失效)")
        return 1
    print("[self-check] PASS")
    return 0


def iter_files(repo: Path):
    for sub in SCAN_ROOTS:
        root = repo / "apps" / "desktop" / sub
        if not root.is_dir():
            print(f"ERROR: 扫描目录不存在: {root}", file=sys.stderr)
            sys.exit(2)
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix not in (EXT_TS | EXT_CSS):
                continue
            parts = p.parts
            if any("node_modules" in part for part in parts):
                continue
            if ".test." in p.name:
                continue
            yield p


def css_selector_for(lines: list[str], line_idx: int, col: int) -> str:
    """从命中位置回溯所在 CSS 规则的选择器(best effort)。

    col = 匹配在行内的列。before=line[:col] 保证同行自闭合规则(含 })时
    找到的 { 必是本规则的开括号;选择器在更上/跨行规则体则向上回溯。
    """
    line = lines[line_idx]
    before = line[:col]
    brace = before.rfind("{")
    if brace != -1 and "}" not in before[brace:]:
        seg_start = max(before.rfind("}", 0, brace), -1)
        sel = before[seg_start + 1 : brace].strip()
        if sel:
            return sel
        # 选择器在更上面(多行选择器/跨行规则体)
        for up in range(line_idx - 1, max(line_idx - 40, -1), -1):
            uline = lines[up]
            ub = uline.rfind("{")
            if ub != -1:
                useg = max(uline.rfind("}", 0, ub), -1)
                sel = uline[useg + 1 : ub].strip()
                if sel:
                    return sel
                return uline.strip()[:120]
    # 跨行规则体:向上找含 { 的行
    for up in range(line_idx - 1, max(line_idx - 40, -1), -1):
        uline = lines[up]
        ub = uline.rfind("{")
        if ub != -1:
            useg = max(uline.rfind("}", 0, ub), -1)
            sel = uline[useg + 1 : ub].strip()
            if sel:
                return sel
    return "?"


def ts_form(line: str) -> str:
    """TS 侧形态启发式:赋值点✅ / 动态拼类名❌ / 标签映射❌ / 类型声明❌ / 常量?"""
    if TS_ASSIGN.search(line):
        return "赋值点"
    if TS_TEMPLATE.search(line):
        return "动态拼类名"
    if TS_TYPE_DECL.search(line):
        return "类型声明"
    if TS_MAP_ENTRY.match(line):
        return "标签映射"
    return "常量/注释?"


def ts_axis(line: str) -> str:
    if AXIS_DECOR.search(line):
        return "装饰?"
    if AXIS_JUDGE_RED.search(line):
        return "判定?"
    if AXIS_JUDGE_GREEN.search(line):
        return "判定?"
    if AXIS_DIR.search(line):
        return "方向?"
    return "?"


def css_axis(selector: str) -> str:
    if AXIS_DECOR.search(selector):
        return "装饰"
    if AXIS_JUDGE_RED.search(selector):
        return "判定"
    if AXIS_JUDGE_GREEN.search(selector):
        return "判定"
    if AXIS_DIR.search(selector):
        return "方向"
    return "?"


def family_of(selector: str, rel: str) -> str:
    m = re.findall(r"\.([A-Za-z0-9_-]+)", selector)
    if m:
        # 取最后一个 class,剥掉常见状态后缀做族键
        last: str = m[-1]
        return last
    return Path(rel).stem


def main() -> int:
    ap = argparse.ArgumentParser(description="语义色 token 三层审计(Step 0)")
    ap.add_argument("--check", action="store_true", help="干跑:只打印,不写审计表(幂等)")
    ap.add_argument("--self-check", action="store_true", help="内嵌阳性 fixture 自检")
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    repo = Path(__file__).resolve().parent.parent
    hits: list[
        dict
    ] = []  # 每条: layer, file, line, form, axis, status, family, selector
    counts = {"L1": 0, "L2": 0, "L3": 0}

    for p in iter_files(repo):
        rel = p.relative_to(repo).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"WARN: 非 UTF-8 跳过: {rel}", file=sys.stderr)
            continue
        lines = text.splitlines()
        is_css = p.suffix == ".css"
        layers = [("L3", RE_L3)] if is_css else [("L1", RE_L1), ("L2", RE_L2)]
        for line_no, line in enumerate(lines):
            for layer, rx in layers:
                m = rx.search(line)
                if not m:
                    continue
                counts[layer] += 1
                col = m.start()
                if is_css:
                    selector = css_selector_for(lines, line_no, col)
                    dead = next(
                        (label for rng, label in DEAD_RULES if (line_no + 1) in rng),
                        None,
                    )
                    form = "渲染点(CSS 规则体)"
                    status = f"死({dead})" if dead else "活"
                    axis = "死" if dead else css_axis(selector)
                    family = family_of(selector, rel)
                else:
                    selector = m.group(0)
                    form = ts_form(line)
                    status = "活" if form == "赋值点" else f"{form}❓"
                    axis = ts_axis(line)
                    family = Path(rel).stem
                hits.append(
                    {
                        "loc": f"{rel}:{line_no + 1}",
                        "selector": selector or "?",
                        "form": form,
                        "carrier": "CSS-var"
                        if is_css
                        else ("TSX-var" if layer == "L2" else "TSX-class"),
                        "axis": axis,
                        "criterion": "行号表精确圈定"
                        if (is_css and "死" in status)
                        else "启发式(待人工复核)",
                        "status": status,
                        "family": family,
                        "layer": layer,
                    }
                )
    # ---- 硬门 ----
    total = sum(counts.values())
    errors = []
    for layer in ("L1", "L2", "L3"):
        if counts[layer] == 0:
            errors.append(
                f"{layer} 计数为 0 —— 空表 ≠ 没有错用!检查引擎是否支持 PCRE lookahead (?!...)"
                "(禁用 grep -E;本脚本应全程 Python re)"
            )
    if not (HARD_MIN <= total <= HARD_MAX):
        errors.append(
            f"总计数 {total} 落在硬门外 [{HARD_MIN}..{HARD_MAX}](基线 {sum(BASELINE.values())} 的 ±30%)"
        )
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # ---- 汇总 ----
    print(
        f"扫描范围: apps/desktop/{{{'|'.join(SCAN_ROOTS)}}} (.ts/.tsx/.css, 排除 *.test.* 与 node_modules)"
    )
    print("口径: 匹配行数(一行内多次命中计 1)")
    warn = False
    for layer, base in BASELINE.items():
        dev = (counts[layer] - base) / base * 100
        flag = "WARN" if abs(dev) > 10 else "ok"
        warn = warn or abs(dev) > 10
        print(
            f"  {layer}: {counts[layer]:4d}  (基线 {base:4d}, 偏差 {dev:+.1f}%) {flag}"
        )
    print(
        f"  合计: {total:4d}  (基线 {sum(BASELINE.values())}, 硬门 [{HARD_MIN}..{HARD_MAX}])"
    )
    dead_n = sum(1 for h in hits if "死" in h["status"])
    print(f"死规则命中: {dead_n} 条(已知死规则 15 条按行号圈定)")

    if args.check:
        print("--check 干跑:未写任何文件(幂等)")
        return 0

    # ---- 写审计表 TSV(utf-8,可 diff,无时间戳) ----
    cols = [
        "loc",
        "selector",
        "form",
        "carrier",
        "axis",
        "criterion",
        "status",
        "family",
    ]
    with REPORT_PATH.open("w", encoding="utf-8", newline="") as f:
        f.write(
            f"# 口径: 匹配行数 | 范围: apps/desktop/{{{'|'.join(SCAN_ROOTS)}}} | 扩展名: .ts,.tsx,.css 排除 *.test.* 与 node_modules\n"
        )
        f.write(
            f"# 基线: L1={BASELINE['L1']} L2={BASELINE['L2']} L3={BASELINE['L3']} (Step1 后;原快照 L3=126)\n"
        )
        f.write(
            "# 判据四级: 类型声明❌→标签映射❌→动态拼类名❌→赋值点✅;轴归属为启发式初判(待人工复核)\n"
        )
        f.write("\t".join(cols) + "\n")
        for h in sorted(hits, key=lambda x: (x["loc"], x["selector"])):
            f.write("\t".join(str(h[c]) for c in cols) + "\n")
    print(f"审计表已写: {REPORT_PATH} ({len(hits)} 行)")
    if warn:
        print("WARN: 存在单层偏差 >10%,先对口径再对代码", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.exit(main())
