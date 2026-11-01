#!/usr/bin/env node
/**
 * i18n codemod —— 把组件里的中文**展示文案**包成 `tr(...)`。
 *
 * ⚠️ 默认 dry-run,不改文件。要真改须显式 `--write`。
 *
 * 设计要点:
 *   1. **用 TypeScript 编译器 API,不用正则。** 正则改 JSX 会在嵌套引号/
 *      模板串/注释里出错(项目历史:手搓 patch 在 autocrlf 下校验必失败)。
 *      AST 能精确区分「JSX 文本」「字符串字面量」「模板串」「注释」。
 *   2. **跳过逻辑值行**(`scripts/i18n-scan-risky.mjs` A 类):`p.side === "多"`
 *      包成 `tr("多")` 会让比较恒 false。豁免按 **file:line** 匹配,不按值
 *      —— 同一个中文词可能在一处是协议值、在另一处是展示文案。
 *   3. **跳过已有 `tr(...)`**:幂等,重复跑不会嵌套成 `tr(tr(...))`。
 *   4. **只在组件函数体内注入 hook**:`const tr = useUiText()` 必须落在
 *      React 组件/自定义 hook 内。文件级纯函数(如 `heartbeat-strip.tsx` 的
 *      `return "窗口已开"`)**不注入** —— 它们的返回串由调用点负责(脚本
 *      单独列出这类,标 `needs-callsite`)。
 *
 * 用法:
 *   node scripts/i18n-codemod.mjs                    # dry-run 全量
 *   node scripts/i18n-codemod.mjs --write            # 真改
 *   node scripts/i18n-codemod.mjs --dir components/watch --write
 *   node scripts/i18n-codemod.mjs --list             # 只出待译清单(供词表登记)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(HERE, "..");
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");

const SKIP_DIRS = new Set([
  "node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo", "scratch",
]);
const SKIP_FILE_RE = /\.(test|spec)\.(ts|tsx|js|jsx)$/;
const CJK = /[一-鿿㐀-䶿]/;

/** 已知无需注入 hook 的文件(已在别处处理)。 */
const HOOK_IMPORT = 'import { useUiText } from "@/components/i18n/use-ui-text"';

/**
 * 读取 risky 扫描器的豁免行(file:line,1-indexed)。
 * 拿不到就返回空集 —— 此时**拒绝写盘**(宁可不改,不可错改)。
 */
function loadExemptLines() {
  const json = path.join(APP_ROOT, "scripts", ".i18n-risky.json");
  try {
    const data = JSON.parse(fs.readFileSync(json, "utf8"));
    return new Set((data.exemptLines ?? []).map((e) => `${e.file}:${e.line}`));
  } catch {
    return null;
  }
}

function walk(dir, out = []) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    if (SKIP_DIRS.has(e.name)) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(e.name) && !SKIP_FILE_RE.test(e.name)) out.push(full);
  }
  return out;
}

/**
 * 判断字符串字面量所处的**渲染位置类别** —— 决定包不包 `tr()`。
 *
 * 分三类,处理方式各不相同:
 *
 *   `jsx`          JSX 文本 / `{...}` 表达式 / 属性值 → **就地包** `tr(...)`
 *   `call-arg`     函数实参(toast/alert/setError…)   → **就地包** `tr(...)`
 *   `data-literal` 对象/数组字面量里的值              → **就地包不了**
 *
 * ⚠️ 为什么 `data-literal` 就地包不了,以及为什么它**不等于**"已处理":
 *   模块级数据结构(`const TABS = [{label:"持仓"}]`)定义在组件外,那里没有
 *   React hook 上下文,`tr()` 用不了。正确修法在**渲染点**
 *   (`{tr(tab.label)}`),与 `sidebar.tsx` 既有的 `tr(SCENE_LABEL[scene])`
 *   同型。
 *   实测:`bottom-drawer.tsx:598 {tab.label}` **没有** tr —— 即该处数据字面量
 *   目前**确实**会在 en 模式漏中文。它们不是"已处理",而是"待渲染点包裹"。
 *   故本脚本把它们单列成 `skipped`,并提示需逐处检查渲染点。
 *
 * 判据(保守,宁少包不漏判成重复):
 *   - 在 JSX 里(文本 / 属性值 / `{...}` 表达式)   → wrap
 *   - 作为**函数调用实参**(非 tr)                  → wrap(toast/alert/setError…)
 *   - 在对象/数组字面量里且不在 JSX 内              → data-literal,不就地包
 *   - 顶层 const 字符串                             → 同上
 */
function classifyPosition(node) {
  // JSX 属性值:title="中文" —— 单独归一类,因为替换要带花括号
  // (title="中文" → title={tr("中文")};直接 title=tr("中文") 是非法 JSX)
  if (node.parent && ts.isJsxAttribute(node.parent) && node.parent.initializer === node) {
    return "jsx-attr";
  }

  let cur = node;
  while (cur.parent) {
    const parent = cur.parent;

    // 命中 JSX 任一形态 → 渲染位
    if (ts.isJsxElement(parent) || ts.isJsxSelfClosingElement(parent) ||
        ts.isJsxFragment(parent) || ts.isJsxExpression(parent) ||
        ts.isJsxAttribute(parent)) {
      return "jsx-expr";
    }

    // 作为调用实参
    if (ts.isCallExpression(parent) && parent.arguments.includes(cur)) {
      const callee = parent.expression;
      const name = ts.isIdentifier(callee)
        ? callee.text
        : ts.isPropertyAccessExpression(callee)
          ? callee.name.text
          : "";
      if (name === "tr") return "already-tr";
      return "call-arg";
    }

    // 对象/数组字面量的值 → 数据位(除非外层是 JSX,已被上面的分支截获)
    if ((ts.isPropertyAssignment(parent) && parent.initializer === cur) ||
        (ts.isArrayLiteralExpression(parent) && parent.elements.includes(cur))) {
      return "data-literal";
    }

    // 变量初始化 / 属性访问等 → 上溯继续找(可能是 JSX 里的 const)
    if (ts.isStatement(parent) || ts.isSourceFile(parent)) break;
    cur = parent;
  }
  return "data-literal";
}

/**
 * 判断一个字符串字面量是否「已经在 tr() 里」——沿 AST 上溯查 CallExpression。
 */
function isInsideTr(node) {
  let cur = node;
  while (cur) {
    const parent = cur.parent;
    if (!parent) return false;
    if (
      ts.isCallExpression(parent) &&
      ts.isIdentifier(parent.expression) &&
      parent.expression.text === "tr"
    ) {
      return true;
    }
    // 跨越语句边界就停(避免把外层别的 tr 误判)
    if (ts.isStatement(parent)) return false;
    cur = parent;
  }
  return false;
}

/**
 * 字符串字面量是否处于**逻辑位置**(==/!= 的操作数、includes 等字符串方法的
 * 实参、case 分支值、`obj["中文"]` 计算键)。
 *
 * ⚠️ 为什么必须靠 AST 而不是豁免清单:`scripts/.i18n-risky.json` 是
 * **file:line** 粒度,任何一次编辑(哪怕只在本文件别处加一行)都会让行号漂移
 * → 豁免失配 → 下一批 codemod 把 `c.evidence_label === "强"` 包成
 * `=== tr("强")`。en 态下 `tr("强")` 返回 `"Strong"`,比较恒 false,
 * 颜色分类**静默失效**(2026-10-16 实测复现,serenity-panel.tsx:367)。
 * 本函数按语法形状判定,与行号无关,是那道清单的**结构性兜底**。
 *
 * 刻意保守:只认字面量**直接**处于逻辑位;`tr(x) === "a"`(左侧已译)之类
 * 不在此列 —— 那是另一类问题,由 A 类扫描负责报告。
 */
function isLogicOperand(node) {
  const parent = node.parent;
  if (!parent) return false;

  // `x === "多"` / `x !== "多"` —— 字面量是任一侧操作数
  if (ts.isBinaryExpression(parent)) {
    const op = parent.operatorToken.kind;
    if (
      op === ts.SyntaxKind.EqualsEqualsToken ||
      op === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsEqualsToken
    ) {
      return true;
    }
  }

  // `s.includes("多")` / indexOf / startsWith / endsWith / match / test
  if (ts.isCallExpression(parent) && parent.arguments.includes(node)) {
    const callee = parent.expression;
    if (ts.isPropertyAccessExpression(callee)) {
      const m = callee.name.text;
      if (["includes", "indexOf", "startsWith", "endsWith", "match", "test"].includes(m)) {
        return true;
      }
    }
  }

  // `case "信号":`
  if (ts.isCaseClause(parent) && parent.expression === node) return true;

  // `obj["中文"]` —— 计算属性键
  if (ts.isElementAccessExpression(parent) && parent.argumentExpression === node) return true;

  return false;
}

/**
 * 找出文件里可作为 hook 注入点的 React 组件/自定义 hook 函数。
 * 判据:函数名首字母大写(组件)或 `use*`(自定义 hook),且返回 JSX 或
 * 用 hook —— 但保守起见只要求命名规则 + 在顶层作用域。
 */
function findInjectTargets(sourceFile) {
  const targets = [];
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name && node.body) {
      const name = node.name.text;
      if (/^[A-Z]/.test(name) || /^use[A-Z]/.test(name)) targets.push(node);
    } else if (
      ts.isVariableDeclaration(node) &&
      node.initializer &&
      (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer)) &&
      ts.isIdentifier(node.name)
    ) {
      const name = node.name.text;
      if ((/^[A-Z]/.test(name) || /^use[A-Z]/.test(name)) && node.initializer.body) {
        targets.push(node.initializer);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return targets;
}

/** 主处理:返回 {edits, pending, needsCallsite}。 */
function processFile(file, exempt) {
  const src = fs.readFileSync(file, "utf8");
  const rel = path.relative(REPO_ROOT, file).replace(/\\/g, "/");
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

  const edits = [];
  const pending = [];
  const skipped = [];
  const needsCallsite = [];
  const alreadyHasHook = /useUiText/.test(src);

  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;

  /** 该位置是否落在豁免行(A 类逻辑值)。 */
  const isExempt = (pos) => {
    if (!exempt) return false;
    return exempt.has(`${rel}:${lineOf(pos)}`);
  };

  const targets = findInjectTargets(sf);
  // ⚠️ 只有 **block body** 的函数能注入 hook:表达式体箭头
  // `const Foo = () => <div>中文</div>` 没有可插 `const tr = …` 的位置。
  // 故「可译范围」只取 block-body 组件;落在此范围外的中文串(模块级 helper
  // 如 `nowInfo(...)` 返回 JSX、表达式体箭头)**一律不包** —— 否则会包出
  // `tr(...)` 而作用域里没有 tr(实测 TS2304 一片)。
  const targetRanges = targets
    .filter((t) => t.body && ts.isBlock(t.body))
    .map((t) => [t.body.getStart(sf), t.body.getEnd()]);

  /** 位置是否落在某个可注入 hook 的组件/自定义 hook 体内(闭包可见 tr)。 */
  const inTarget = (pos) => targetRanges.some(([s, e]) => pos >= s && pos < e);

  /** 含中文的字符串字面量 / JSX 文本 / 模板串 → 收集。 */
  const visit = (node) => {
    if (ts.isJsxText(node)) {
      // JSX 文本子节点:<div>中文</div>
      const text = node.text;
      const trimmed = text.trim();
      if (trimmed && CJK.test(trimmed) && !isExempt(node.getStart(sf))) {
        // ⚠️ 偏移必须锚在 `getEnd() - text.length`,**不能**用 `getStart()`:
        // JSX 文本的前导空白被 scan 当作 trivia,`getStart()` 会跳过它,而
        // `node.text` 却**包含**它。实测 `" 步)"`(3 字符)getStart()=49 而
        // 真实起点是 48 → 若再叠加 `indexOf(trimmed)` 就双计偏移,
        // end 会落在 `)<` 上,把 `</span>` 的 `<` 吃掉(产出坏 JSX)。
        const nodeStart = node.getEnd() - text.length;
        const idx = text.indexOf(trimmed);
        const start = nodeStart + idx;
        // ⚠️ 源串值必须用 **JSX 折叠后**的形态,不是原文。JSX 渲染时会把
        //「换行 + 行首缩进」折成**一个空格**,而 `node.text` 带着原始
        // `\r\n` + 缩进。直接拿去当键会产出
        // `tr("…,非 AI\r\n                  重演判断…")` —— 人眼看不见、
        // 手写译文无法可靠复现,且与重新格式化后再跑 codemod 的产物不一致。
        // 实测踩过 6 处(strategy-workspace / analysis-panel 等)。
        // 编辑范围(start/end)仍按原文算,只有键值折叠。
        const value = trimmed.replace(/\s*[\r\n]+\s*/g, " ");
        if (!inTarget(start)) {
          // 模块级 helper(如 `nowInfo()` 返回 JSX)里的中文 —— 那片作用域
          // 没有 tr。不包,交调用点处理。
          needsCallsite.push({ file: rel, line: lineOf(node.getStart(sf)), value });
        } else {
          edits.push({ start, end: start + trimmed.length, kind: "jsx-text", value });
          pending.push({ file: rel, line: lineOf(node.getStart(sf)), value });
        }
      }
    } else if (ts.isStringLiteral(node) && CJK.test(node.text) &&
               !isExempt(node.getStart(sf)) && !isInsideTr(node)) {
      // 字符串字面量 —— 按所处位置决定包/不包
      const pos = classifyPosition(node);
      if (isLogicOperand(node)) {
        // ⚠️ 结构性兜底(先于 pos 分类):==/!= 操作数、字符串方法实参、
        // case 值、计算键 —— 包成 tr() 会让比较恒 false。见 isLogicOperand
        // 的注释:豁免清单按 file:line 匹配,行号一漂就失配,这道 AST 判据
        // 与行号无关。归入 skipped(它确实不是渲染文案,是协议值)。
        skipped.push({
          file: rel,
          line: lineOf(node.getStart(sf)),
          value: node.text,
          reason: "logic-operand",
        });
      } else if (pos === "data-literal") {
        skipped.push({
          file: rel,
          line: lineOf(node.getStart(sf)),
          value: node.text,
          reason: pos,
        });
      } else if (!inTarget(node.getStart(sf))) {
        // 渲染位形态,但不在任何可注入 hook 的函数体内(模块级 helper /
        // 表达式体箭头)→ 包了会 TS2304。交调用点。(call-arg 尤须留意:
        // 模块级 `toastError("请选择…")` 是常见来源。)
        needsCallsite.push({ file: rel, line: lineOf(node.getStart(sf)), value: node.text });
      } else {
        edits.push({
          start: node.getStart(sf),
          end: node.getEnd(),
          kind: pos, // jsx-attr / jsx-expr / call-arg
          value: node.text,
        });
        pending.push({ file: rel, line: lineOf(node.getStart(sf)), value: node.text });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);

  // 注:原「本文件无 hook 注入点则整文件转 needsCallsite」的收尾分支已删 ——
  // 上面的 inTarget 守卫已逐条把关(edits 非空 ⟹ 必有 block-body 组件),
  // 留着只是不可达的死代码。

  return { rel, src, sf, edits, pending, skipped, needsCallsite, alreadyHasHook, targets };
}

function main() {
  const args = process.argv.slice(2);
  const write = args.includes("--write");
  const listOnly = args.includes("--list");
  const dirArg = args.indexOf("--dir");
  const target = dirArg >= 0 ? path.resolve(APP_ROOT, args[dirArg + 1]) : APP_ROOT;

  // --files a.tsx,b.tsx —— 精确到文件的批处理。--dir 只能整目录,而
  // components/research 有 71 个文件、1675 处,一次全批无法逐批复核 diff。
  const filesArg = args.indexOf("--files");
  const explicitFiles =
    filesArg >= 0
      ? args[filesArg + 1]
          .split(",")
          .map((f) => f.trim())
          .filter(Boolean)
          .map((f) => path.resolve(APP_ROOT, f))
      : null;

  const exempt = loadExemptLines();
  if (write && !exempt) {
    console.error(
      "拒绝写盘:未找到 scripts/.i18n-risky.json。\n" +
        "先跑:`node scripts/i18n-scan-risky.mjs --json > scripts/.i18n-risky.json`\n" +
        "缺少豁免清单时改文件可能把 A 类逻辑值包进 tr() → 逻辑静默失效。",
    );
    process.exit(2);
  }

  const files = explicitFiles ?? walk(target);
  const results = files.map((f) => processFile(f, exempt));
  const withEdits = results.filter((r) => r.edits.length > 0);

  const allPending = results.flatMap((r) => r.pending);
  const allNeedsCallsite = results.flatMap((r) => r.needsCallsite);

  if (listOnly) {
    const uniq = [...new Set(allPending.map((p) => p.value))].sort();
    console.log(JSON.stringify({ total: allPending.length, unique: uniq.length, values: uniq }, null, 2));
    return;
  }

  // dry-run 报告
  let totalEdits = 0;
  for (const r of withEdits) {
    totalEdits += r.edits.length;
    const kinds = {};
    for (const e of r.edits) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
    const kindStr = Object.entries(kinds).map(([k, v]) => `${k}×${v}`).join(" ");
    const flag = r.targets.length === 0 ? "  ⚠️ 无 hook 注入点(调用点处理)" : "";
    console.log(`  ${r.rel}  ${r.edits.length} 处  [${kindStr}]${flag}`);
  }
  console.log(`\n合计:${withEdits.length} 文件 / ${totalEdits} 处待包;${allPending.length} 条文案,去重 ${new Set(allPending.map((p) => p.value)).size} 条。`);

  const allSkipped = results.flatMap((r) => r.skipped);
  if (allSkipped.length > 0) {
    // 区分:文件里已有 tr() → 渲染点**可能**已处理;完全没有 → 基本确定漏中文
    const fileHasTr = new Map(results.map((r) => [r.rel, /\btr\(/.test(r.src)]));
    const noTr = allSkipped.filter((p) => !fileHasTr.get(p.file));
    console.log(`\n⏭️  数据字面量 ${allSkipped.length} 处(不在渲染位,须检查渲染点是否已 tr):`);
    console.log(`   ├─ 同文件已有 tr() : ${allSkipped.length - noTr.length} 处(渲染点可能已处理)`);
    console.log(`   └─ 同文件无 tr()   : ${noTr.length} 处(⚠️ 基本确定 en 模式会漏中文)`);
    for (const p of noTr.slice(0, 12)) {
      console.log(`        ${p.file}:${p.line}  "${p.value}"`);
    }
    if (noTr.length > 12) console.log(`        … 另 ${noTr.length - 12} 处`);
  }

  if (allNeedsCallsite.length > 0) {
    console.log(`⚠️ ${allNeedsCallsite.length} 处位于纯函数模块(无 hook),须调用点包裹:`);
    for (const p of allNeedsCallsite.slice(0, 15)) console.log(`     ${p.file}:${p.line}  "${p.value}"`);
  }
  if (exempt) console.log(`\n豁免行(A 类逻辑值):${exempt.size} 处(按 file:line 匹配)`);
  else console.log("\n⚠️ 未加载豁免清单 —— --write 会被拒绝。");

  if (!write) {
    console.log("\n(dry-run:未改动任何文件。加 --write 真改。)");
    return;
  }

  // ── --write:应用编辑 ────────────────────────────────────────────
  let changedFiles = 0;
  let appliedEdits = 0;
  let injectedHooks = 0;

  for (const r of results) {
    if (r.edits.length === 0) continue;
    let out = r.src;

    // 插入的行尾沿用被改文件的既有风格(CRLF 文件插 `\r\n`)。
    // 曾一律插 `\n`:本仓 `core.autocrlf=true`,工作区 CRLF,于是注入的
    // import 与 hook 两行成了全文仅有的裸 LF 行 —— 文件行尾混杂。
    // ⚠️实测这**不影响行号**(TS 的 getLineAndCharacterOfPosition 与
    // `split(/\r?\n/)` 都数得对),所以不是任何"偏移"的病因;纯粹是
    // 让工作区保持单一风格 —— 本仓历史上 CRLF/LF 混用已造成过一次
    // 合并假冲突(见 memory: merge-preview-must-normalize-line-endings)。
    const eol = out.includes("\r\n") ? "\r\n" : "\n";
    const NL = (s) => s.replace(/\n/g, eol);

    // 1) 收集插入点:hook 注入(函数体首行)+ import
    const inserts = [];

    // hook 注入 —— 仅 block body 可注入;表达式体箭头函数列 needsCallsite
    for (const t of r.targets) {
      if (!t.body) continue;
      if (!ts.isBlock(t.body)) continue; // () => expr 形式,无法插 hook
      // 该函数体内是否真有本次编辑?
      const [bs, be] = [t.body.getStart(r.sf), t.body.getEnd()];
      const hasEdit = r.edits.some((e) => e.start >= bs && e.start < be);
      if (!hasEdit) continue;
      // 已有 tr/useUiText 绑定则跳过
      if (/\bconst\s+tr\s*=/.test(out.slice(bs, be))) continue;
      // ⚠️ 注入点不能假定 `{` 后必是换行:单行函数体 `{ return <div>中文</div> }`
      // 会拼成 `const tr = useUiText() return …`(ASI 接不上 → 语法错)。
      const brace = out.indexOf("{", bs);
      const after = out[brace + 1];
      inserts.push({
        pos: brace + 1,
        text: NL(after === "\n" || after === undefined ? "\n  const tr = useUiText()" : "\n  const tr = useUiText()\n"),
      });
      injectedHooks++;
    }

    // import 注入 —— 已有则不重复。
    // ⚠️ 必须走 AST 取**最后一条 import 声明**的 getEnd()。曾用
    // `/^import[^\n]*\n/gm` 逐行扫,碰到多行 import(`import {\n  A,\n} from "x"`)
    // 只匹配到首行 `import {`,注入点落进花括号内 → 产出
    // `import {\nimport { useUiText } …` 坏语法(tsc 报一片 TS1003)。
    // AST 节点的 getEnd() 天然覆盖多行,不受换行/注释影响。
    if (!/from\s+["']@\/components\/i18n\/use-ui-text["']/.test(out)) {
      let lastImportEnd = 0;
      for (const stmt of r.sf.statements) {
        if (ts.isImportDeclaration(stmt)) lastImportEnd = stmt.getEnd();
      }
      if (lastImportEnd === 0) {
        console.error(`  跳过 ${r.rel}:找不到 import 块,不注入(hook 会未定义)`);
        continue;
      }
      inserts.push({ pos: lastImportEnd, text: NL(`\n${HOOK_IMPORT}`) });
    }

    // 2) 字符串/JSX 编辑 + 插入点,统一按位置**倒序**应用
    //    (倒序保证前面的位置偏移不影响后面的 start/end)
    const ops = [
      ...r.edits.map((e) => ({ start: e.start, end: e.end, text: `{tr(${JSON.stringify(e.value)})}`, raw: e })),
      ...inserts.map((i) => ({ start: i.pos, end: i.pos, text: i.text })),
    ];
    ops.sort((a, b) => b.start - a.start);

    for (const op of ops) {
      // 按位置类别决定包装形态:
      //   jsx-text   JSX 裸文本    <span>中文</span>   → {tr("中文")}
      //   jsx-attr   属性值字面量  title="中文"        → {tr("中文")}(含花括号!)
      //   jsx-expr   已在 {} 内的表达式 / 已是表达式位 → tr("中文")
      //   call-arg   函数实参      toastInfo("中文")   → tr("中文")
      let text;
      if (op.raw && op.raw.kind === "jsx-text") {
        text = `{tr(${JSON.stringify(op.raw.value)})}`;
      } else if (op.raw && op.raw.kind === "jsx-attr") {
        text = `{tr(${JSON.stringify(op.raw.value)})}`;
      } else if (op.raw) {
        text = `tr(${JSON.stringify(op.raw.value)})`;
      } else {
        text = op.text; // hook / import 注入
      }
      out = out.slice(0, op.start) + text + out.slice(op.end);
      if (op.raw) appliedEdits++;
    }

    fs.writeFileSync(r.sf.fileName, out, "utf8");
    changedFiles++;
  }

  console.log(`\n✅ 已改 ${changedFiles} 文件 / ${appliedEdits} 处文案 / 注入 ${injectedHooks} 个 hook。`);
  console.log("   下一步必做:1) npx tsc --noEmit  2) npx vitest run  3) 复核 diff");
}

main();
