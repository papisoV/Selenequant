#!/usr/bin/env python3
"""前端响应字段 × 后端生产点 契约校验（防"死字段"复发）。

## 背景 / 根因

119 个前端组件各自内联声明响应类型，后端是 pydantic 模型，**没有任何东西
校验两边的字段级一致性**。加之：

1. TS 可选字段（`field?: T`）让任意 JSON 都能通过编译 —— 漂移永不报错；
2. `?.` 可选链把"字段不存在"静默降级成 `undefined` → 渲染成 `—` / `0`；
3. 于是页面显示的可能是**永远为空的假字段**，而非报错。

2026-10 的死字段扫描（见 scratch/dead-field-scan.md）人工逐条裁决出 3 P0 +
11 P1 项，修掉了两个批次（7a2c2aba / e3f59fec）。本脚本把那次一次性扫描
固化为**可重复执行的字段级契约校验**，是防复发的唯一根治手段。

## 判定口径（关键，勿改）

后端"生产"= 该字段名在**构造点被显式填充**，取自四类语法证据：
  1. 任意调用点的关键字实参      `Fn(field=...)` / `Model(field=...)`
  2. 字典字面量字符串键          `{"field": ...}`
  3. 下标赋值                    `row["field"] = ...`
  4. SQL 别名                    `SELECT ... AS field`

**不**把"在 pydantic 模型里声明"算作生产点 —— 声明 ≠ 填充。实测反例：
  - `TradeSetup.take_profit_price_2`（models.py:104 声明，flow.py:523 构造
    时未传）→ 真死字段；
  - `ToxicOrderflowScore.toxicity_score`（tof.py:131 构造时传了）→ 真字段。
若把"声明"也算生产，上面两类无法区分，正是原扫描误报 121 条的根因。

## 基线棘轮（ratchet）

静态分析不可能 100% 精确（例如 `SELECT *` 取整表列、`**kwargs` 展开、
跨语言同名巧合）。因此用**已提交的基线快照** `scripts/dead_field_baseline.json`
记录"当前已知且已人工复核"的条目：

  - 出现**基线之外**的新漂移 → 退出码 1（挡住新债）；
  - 基线条目已消失（字段被修好或删除）→ 仅提示刷新，不阻断。

即：只拦**新增**漂移，存量已复核项不反复报警。这与
`lib/page-manifest.test.ts` 的 `KNOWN_UNREGISTERED_SLUGS` 同构。

## 已知局限（宁可漏报，不可误报）

本检查用**字段名等价**判定前后端接线，以下情形**不会**被捕获，
属已知盲区（需人工/其他手段兜底）：

  1. **纯语义改名**：`sessionId` ↔ `session_id` 这类 snake↔camel 已归一
     （见 `_norm_names`），但**语义改名**（如 `ts` ↔ `trade_setup`）仍看不见。
  2. **同名碰撞**：前端某字段恰好与无关的后端变量/kwarg 同名 → 假阴性
     （误判为已接线）。例如 `used` 可能撞上无关的 `used=` kwarg。
  3. **动态构造**：`Model(**kwargs)` 展开、`SELECT *` 取整表列 → 后端生产点
     静态不可识别 → 可能假阳性。**部分已覆盖**：把模型 JSON schema 渲染进
     prompt（`Cls.model_json_schema()`）的动态写入通路已识别（见
     `scan_backend`），但 `**kwargs` 与 `SELECT *` 仍在盲区。

这些盲区正是需要**基线棘轮**而非"零容忍"的原因：检查只保证
"没引入**新的、静态可识别的**前后端字段漂移"。

## 用法

  python scripts/check_dead_fields.py            # 校验（CI / pre-commit）
  python scripts/check_dead_fields.py --report   # 打印完整候选（人工复核用）
  python scripts/check_dead_fields.py --update-baseline   # 刷新基线快照

退出码：0=无新增死字段；1=发现新增死字段。
"""

from __future__ import annotations

import ast
import collections
import json
import os
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "scripts" / "dead_field_baseline.json"

FRONTEND_ROOT = ROOT / "apps" / "desktop"
BACKEND_ROOTS = [ROOT / "packages", ROOT / "services", ROOT / "apps"]

EXCL_DIRS = {
    "node_modules",
    ".next",
    "dist",
    "out",
    ".turbo",
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    "scratch",
}

# useApi<T> / fetch<T> 等泛型调用 —— 用于识别"API 响应类型"
RESP_GENERIC_RE = re.compile(
    r"\b(?:useApi|useApiPage|fetch|apiFetch|apiGet|apiPost|apiPut|apiDelete"
    r"|request|client)\s*<\s*([A-Za-z_$][\w$]*)"
)
IFACE_HDR_RE = re.compile(
    r"(?:export\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)\s*(?:=\s*)?\{"
)
FIELD_RE = re.compile(r"^\s*(?:readonly\s+)?([a-zA-Z_$][\w$]*)\s*\??\s*:\s*(.+?);?\s*$")
SQL_AS_RE = re.compile(r"\bAS\s+([a-zA-Z_]\w*)", re.IGNORECASE)
# 渲染信号：成员访问 `data.fld` / 解构 `{fld}` `, fld}` `{fld,`
RENDER_MEMBER_RE = re.compile(r"\.([A-Za-z_$][\w$]*)")
RENDER_DESTRUCTURE_RE = re.compile(r"(?:\{|,)\s*([A-Za-z_$][\w$]*)\s*(?=[,}])")

# 字段声明的"非声明关键字"排除表（interface 体内可能出现的方法签名/关键字）
FIELD_STOPWORDS = {"if", "else", "return", "const", "let", "function", "new"}

# pydantic 类基名：class Foo(BaseModel) 视为模型类，其字段可被动态构造填充。
PY_MODEL_BASES = {
    "BaseModel",
    "BaseSettings",
    "TypedDict",
    "NamedTuple",
    "SQLModel",
    "RootModel",
}
# 把模型 JSON schema 渲染出来的调用：`Cls.model_json_schema()`（旧名 `.schema()`）。
# 这是"字段被**告知 LLM**"的信号——schema 入 prompt = 契约里请求了该字段，
# 故此类模型的全部字段（含嵌套）计为可达。见下方 _schema_emitted_classes。
SCHEMA_ATTRS = {"model_json_schema", "schema"}

# SQL 里出现在 SELECT 列表中的裸列名（无 AS 别名），如
# `SELECT id, symbol, payload_json FROM events_recent` → 捕获 payload_json。
SQL_SELECT_RE = re.compile(r"\bSELECT\b(.*?)\bFROM\b", re.IGNORECASE | re.DOTALL)
SQL_BARE_COL_RE = re.compile(
    r"(?:^|[,(\s])([a-zA-Z_]\w*)\s*(?=[,)]|\s+AS\b|$)", re.IGNORECASE
)
SQL_KEYWORDS = {
    "select",
    "from",
    "where",
    "and",
    "or",
    "as",
    "on",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "group",
    "order",
    "by",
    "having",
    "limit",
    "offset",
    "case",
    "when",
    "then",
    "else",
    "end",
    "distinct",
    "with",
    "union",
    "all",
    "asc",
    "desc",
    "null",
    "not",
    "in",
    "is",
    "count",
    "sum",
    "max",
    "min",
    "avg",
    "round",
    "cast",
    "coalesce",
    "ifnull",
    "nullif",
    "json_extract",
    "json_valid",
    "row_number",
    "over",
    "partition",
    "values",
    "into",
    "set",
    "as",
    "using",
    "cross",
    "full",
    "natural",
}


def _walk(base: Path, exts: tuple[str, ...]):
    if not base.exists():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in EXCL_DIRS]
        for fn in filenames:
            if fn.endswith(exts):
                yield Path(dirpath) / fn


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------
# 前端：声明类型 + 响应类型 + 渲染字段
# --------------------------------------------------------------------------


def _is_test_file(path: Path) -> bool:
    p = str(path).replace("\\", "/").lower()
    return ".test." in p or ".spec." in p or "/__tests__/" in p or "/tests/" in p


def scan_frontend() -> (
    tuple[dict[tuple[str, str], dict], set[tuple[str, str]], set[str]]
):
    """返回 (types, resp_types, rendered)。

    - types: {(相对文件, 类型名): {"fields": [字段名]}}
      **按文件键控**：同名 interface 在多个文件里各有一份（实测 3 个不同的
      `Position`），按名字合并会串味、把别的文件的字段算进来。
    - resp_types: 被当作 API 响应类型使用的 (文件, 类型名) 集合
    - rendered: 在**生产** .tsx 中真实渲染过的字段名集合（成员访问/解构/属性）。
      测试文件不计入 —— 测试里出现某字段不代表页面会渲染它。
    """
    src = list(_walk(FRONTEND_ROOT, (".ts", ".tsx")))

    types: dict[tuple[str, str], dict] = {}
    for f in src:
        text = _read(f)
        if not text:
            continue
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            m = IFACE_HDR_RE.search(lines[i])
            if not m:
                i += 1
                continue
            name = m.group(1)
            key = (rel, name)
            depth = lines[i].count("{") - lines[i].count("}")
            entry = types.setdefault(key, {"fields": []})
            i += 1
            while i < len(lines) and depth > 0:
                depth += lines[i].count("{") - lines[i].count("}")
                fm = FIELD_RE.match(lines[i])
                if fm and fm.group(1) not in FIELD_STOPWORDS:
                    entry["fields"].append(fm.group(1))
                i += 1

    resp_types: set[tuple[str, str]] = set()
    for f in src:
        text = _read(f)
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        is_api_file = "/lib/api/" in rel or "/api/" in rel
        for m in RESP_GENERIC_RE.finditer(text):
            # 泛型位置引用的类型：在该文件或同名类型所在文件里认领
            for key in types:
                if key[1] == m.group(1) and (key[0] == rel or not is_api_file):
                    resp_types.add(key)
        if is_api_file:
            for key in types:
                if key[0] == rel:
                    resp_types.add(key)

    resp_types = {k for k in resp_types if k in types}

    # 渲染信号：字段名以成员访问 `.fld` / 解构 `{fld}` / 属性 `fld:` 形式出现。
    # 不用"裸标识符出现"——那样注释、字符串、className 里的同名词都会误判为渲染。
    rendered: set[str] = set()
    for f in _walk(FRONTEND_ROOT, (".tsx",)):
        if _is_test_file(f):
            continue
        text = _read(f)
        rendered.update(RENDER_MEMBER_RE.findall(text))
        rendered.update(RENDER_DESTRUCTURE_RE.findall(text))
        rendered.update(
            re.findall(r"(?:^|[{,;\s])([a-zA-Z_$][\w$]*)\s*:", text, re.MULTILINE)
        )

    return types, resp_types, rendered


# --------------------------------------------------------------------------
# 后端：构造点生产集（AST + SQL 别名）
# --------------------------------------------------------------------------


def _annotation_names(node: ast.AST) -> set[str]:
    """注解里出现的标识符。

    `TradeSetup` / `TradeSetup | None` / `list[TradeSetup]` → {"TradeSetup"}
    （Python 3.10+ 的 `X | None` 是 BinOp，3.9 的 `Optional[X]` 是 Subscript，
    两种都靠遍历 Name/Attribute 覆盖）。
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _model_classes(tree: ast.AST) -> dict[str, dict[str, set[str]]]:
    """{类名: {字段名: 注解引用的类型名}} —— 只收 pydantic 模型类。

    基名在 PY_MODEL_BASES 里的才算（`class Foo(BaseModel)`），普通
    dataclass / 任意类不算，避免把无关类的字段当成"可被动态填充"。
    """
    out: dict[str, dict[str, set[str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        bases |= {b.attr for b in node.bases if isinstance(b, ast.Attribute)}
        if not (bases & PY_MODEL_BASES):
            continue
        fields: dict[str, set[str]] = {}
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields[stmt.target.id] = _annotation_names(stmt.annotation)
        if fields:
            out.setdefault(node.name, {}).update(fields)
    return out


def scan_backend() -> collections.Counter:
    """返回各字段名在后端"可达"的计数。

    可达 = 显式构造点填充（kwarg / 字典键 / 下标赋值 / SQL 列）
         ∪ schema 入 prompt 的模型字段（含嵌套）——后者是"告知 LLM"的动态
         写入通路，字段不经过 kwarg 但确有真实来源，见下方注释。
    """
    produced: collections.Counter = collections.Counter()
    files = []
    for r in BACKEND_ROOTS:
        files.extend(_walk(r, (".py",)))

    model_classes: dict[str, dict[str, set[str]]] = {}
    schema_calls: set[str] = set()

    for f in files:
        text = _read(f)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        model_classes.update(_model_classes(tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg:
                        produced[kw.arg] += 1
            elif isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        produced[k.value] += 1
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and isinstance(tgt.slice.value, str)
                    ):
                        produced[tgt.slice.value] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in SQL_AS_RE.finditer(node.value):
                    produced[m.group(1)] += 1
                # 裸 SELECT 列名（无 AS）：`SELECT a, b, payload_json FROM t`。
                # 先剥掉 `AS alias` 子句（别名已由上面计数）与括号内函数实参
                # （`AVG(x)` 的 x 不是列），再取剩下的裸标识符。
                for sel in SQL_SELECT_RE.finditer(node.value):
                    chunk = re.sub(
                        r"\bAS\b\s+\w+", " ", sel.group(1), flags=re.IGNORECASE
                    )
                    chunk = re.sub(r"\([^()]*\)", " ", chunk)
                    for col in SQL_BARE_COL_RE.finditer(chunk):
                        name = col.group(1)
                        if name.lower() not in SQL_KEYWORDS:
                            produced[name] += 1

        # schema 入 prompt 的模型：XXX.model_json_schema() 写入提示词，
        # LLM 回填后 model_validate 填充字段——有真实写入通路，只是不经过 kwarg。
        # 只认**具名类**的调用（`Cls.model_json_schema()`）；泛型 helper
        # （`def get_json_schema(model_cls): model_cls.model_json_schema()`）
        # 的参数不是具名类，自然不匹配——避免把"任何模型"一律放过。
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SCHEMA_ATTRS
                and isinstance(node.func.value, ast.Name)
            ):
                schema_calls.add(node.func.value.id)

    # 传递闭包：直接发 schema 的类，加上其字段注解引用到的模型类（嵌套结构）。
    emitted: set[str] = set()
    stack = [c for c in schema_calls if c in model_classes]
    while stack:
        cls = stack.pop()
        if cls in emitted:
            continue
        emitted.add(cls)
        for ann_names in model_classes.get(cls, {}).values():
            for name in ann_names:
                if name in model_classes and name not in emitted:
                    stack.append(name)

    for cls in emitted:
        for fld in model_classes[cls]:
            produced[fld] += 1

    return produced


# --------------------------------------------------------------------------
# 交叉判定
# --------------------------------------------------------------------------


def _norm_names(name: str) -> set[str]:
    """字段名的等价写法集合：原名 + snake_case + camelCase。

    前后端常各用一套命名（后端 `session_id` ↔ 前端 `sessionId`）。只按字面
    相等比较会把"接了线但改了名"误判成死字段（已知盲区①）。归一到两种写法
    后，任一侧的任一写法命中即视为接线。
    """
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    parts = snake.split("_")
    camel = parts[0] + "".join(p.title() for p in parts[1:]) if parts else name
    return {name, snake, camel}


def compute_candidates() -> list[dict]:
    types, resp_types, rendered = scan_frontend()
    produced = scan_backend()

    # 把后端可达字段名摊平成"归一后等价类"集合，供前后端跨命名比较。
    produced_norm: set[str] = set()
    for name in produced:
        produced_norm |= _norm_names(name)

    candidates: list[dict] = []
    for rel, tname in sorted(resp_types):
        for fld in types[(rel, tname)]["fields"]:
            if fld not in rendered:  # 前端从未渲染 → 不构成"页面假字段"
                continue
            # 后端任一命名的等价写法命中即视为已接线（含 snake↔camel）。
            if _norm_names(fld) & produced_norm:
                continue
            candidates.append({"type": tname, "field": fld, "decl_file": rel})
    return candidates


def _key(c: dict) -> tuple[str, str, str]:
    """基线身份 = (声明文件, 类型名, 字段名)。必须含文件：同名 interface 在
    多文件各有一份，只按 (类型,字段) 会串味、丢条目。"""
    return (c["decl_file"], c["type"], c["field"])


def load_baseline() -> dict[tuple[str, str, str], str]:
    """返回 {(声明文件, 类型, 字段): 复核理由}。"""
    if not BASELINE_PATH.exists():
        return {}
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[tuple[str, str, str], str] = {}
    for item in data.get("known", []):
        if isinstance(item, dict):
            out[(item["file"], item["type"], item["field"])] = item.get("reason", "")
    return out


def write_baseline(
    candidates: list[dict], reasons: dict[tuple[str, str, str], str]
) -> None:
    """写基线。已有条目的理由原样保留；新条目标注待复核。"""
    payload = {
        "version": 1,
        "note": (
            "已人工复核的死字段基线（棘轮）。check_dead_fields.py 仅对基线之外的"
            "新增漂移报错；条目消失只提示刷新。刷新："
            "python scripts/check_dead_fields.py --update-baseline"
        ),
        "known": [
            {
                "file": c["decl_file"],
                "type": c["type"],
                "field": c["field"],
                "reason": reasons.get(_key(c), "UNREVIEWED — 待裁决"),
            }
            for c in sorted(candidates, key=_key)
        ],
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv: list[str]) -> int:
    candidates = compute_candidates()
    current = {_key(c) for c in candidates}

    if "--update-baseline" in argv:
        reasons = load_baseline()
        write_baseline(candidates, reasons)
        print(
            f"[dead-field] baseline 已刷新：{len(current)} 条 → {BASELINE_PATH.relative_to(ROOT)}"
        )
        return 0

    if "--report" in argv:
        print(f"[dead-field] 当前候选 {len(candidates)} 条：")
        for c in candidates:
            print(f"  {c['field']:24s} {c['type']:24s} {c['decl_file']}")
        return 0

    baseline = load_baseline()
    base_keys = set(baseline)
    new = sorted(current - base_keys)
    stale = sorted(base_keys - current)

    if stale:
        print(f"[dead-field] 提示：{len(stale)} 条基线条目已消失（字段已修复/删除）：")
        for rel, t, f in stale:
            print(f"    {t}.{f}  ({rel})")
        print(
            "  可运行 `python scripts/check_dead_fields.py --update-baseline` 刷新基线。"
        )

    if new:
        print("[dead-field] FAIL：发现新的死字段（前端渲染、后端无构造点填充）")
        print("  含义：前端类型声明并渲染了该字段，但后端从未在任何构造点填充它")
        print("  → 页面该字段会静默显示为 `—`/`0`，或整块区域永远为空。")
        print("  修法二选一：(a) 后端补上填充 或 (b) 前端删除该字段与其渲染；")
        print("  若确认为已复核的存量项，将其加入 scripts/dead_field_baseline.json。")
        print()
        for rel, t, f in new:
            print(f"  {f:24s} {t:24s} {rel}")
        return 1

    print(f"[dead-field] OK：无新增死字段（候选 {len(current)}，基线 {len(baseline)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
