#!/usr/bin/env node
/**
 * i18n 逻辑值扫描器 —— 找出「中文当逻辑值」的行,产出 DO-NOT-WRAP 豁免清单。
 *
 * 背景:`tr()` 是「按中文源串查表」的翻译函数。它对**展示文案**安全,对
 * **数据契约值**致命 —— 例如 `p.side === "多"` 里的 `"多"` 是与后端约定的
 * 协议值,若被 codemod 包成 `tr("多")`,en 模式下就变成 `"Long" === "多"` 恒 false,
 * 逻辑静默失效(可能连测试都测不出,视断言写法而定)。
 *
 * 本脚本扫出所有「中文串出现在逻辑位置」的行,作为:
 *   1. `scripts/i18n-codemod.mjs` 的跳过清单(不包 tr)
 *   2. 人工复核清单(逐条确认是协议值而非展示文案)
 *   3. `lib/i18n/values.ts` 的登记依据(这些值改在**展示点**映射)
 *
 * ⚠️ 豁免是**行级**(file:line),不是值级 —— 同一个中文词可能在一处是协议值、
 * 在另一处是展示文案。实测反例:`"多"` 在 `bottom-drawer.tsx:418` 是
 * `p.side === "多"`(协议值),在 `bottom-drawer.tsx:87` 是三元展示分支
 * (`多`/`空` 标签)。值级豁免会把后者一并跳过 → 该处中文在 en 模式残留。
 * 故 JSON 输出同时给出 line 级 occurrences 与 value 级清单,消费方须按
 * file:line 匹配。
 *
 * 识别模式(右操作数含中文):
 *   === / !== / == / !=     相等比较
 *   .includes(...)          子串判断
 *   .indexOf(...)           位置查找
 *   .startsWith/.endsWith   前后缀判断
 *   .match/.test            正则
 *   case "中文"             switch 分支
 *   ["中文"] / {"中文":..}   中文当键(对象索引 / 字面量键)
 *   .find(x => x.kind === "中文")  谓词(由上面的 === 覆盖)
 *
 * 用法:
 *   node scripts/i18n-scan-risky.mjs                # 人类可读报告
 *   node scripts/i18n-scan-risky.mjs --json         # 机器可读(供 codemod 消费)
 *   node scripts/i18n-scan-risky.mjs --check        # CI 模式:有新增未豁免即非零退出
 *   node scripts/i18n-scan-risky.mjs --dir <path>   # 限定扫描目录(默认 apps/desktop)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// 本脚本位于 <repo>/apps/desktop/scripts/,故应用根往上 1 层、仓库根往上 3 层。
const APP_ROOT = path.resolve(HERE, "..");
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const DEFAULT_DIR = APP_ROOT;

// ⚠️ `scratch` 必须在内:codemod 的试跑副本落在此目录
// (`scratch/tmp/cm*/components/…`),不排掉会把**自己生成的临时拷贝**当成源码扫,
// 产出幽灵条目 + 幽灵豁免行。与 i18n-codemod.mjs 的 SKIP_DIRS 保持一致。
const SKIP_DIRS = new Set([
  "node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo", "scratch",
]);
const SKIP_FILE_RE = /\.(test|spec)\.(ts|tsx|js|jsx)$/;
const SCAN_EXT = /\.(ts|tsx)$/;

/** 中文统一表意文字(CJK 基本区 + 扩展 A)。 */
const CJK = /[一-鿿㐀-䶿]/;

/**
 * 逻辑位置模式 —— 每条捕获「右/内侧操作数」。
 * 刻意保守:只认右操作数**直接是字符串字面量**的情形,避免把
 * `x.includes(tr("…"))` 这类(左侧翻译、右侧纯逻辑)误判。
 */
const LOGIC_PATTERNS = [
  // 相等比较:左侧任意,右侧字符串字面量
  { name: "equality", re: /[=!]==?\s*(["'`])((?:(?!\1).)+)\1/g, group: 2 },
  // 字符串方法:includes / indexOf / startsWith / endsWith / match / test
  {
    name: "string-method",
    re: /\.(includes|indexOf|startsWith|endsWith|match|test)\s*\(\s*(["'`])((?:(?!\2).)+)\2/g,
    group: 3,
  },
  // switch 分支
  { name: "case", re: /\bcase\s+(["'`])((?:(?!\1).)+)\1/g, group: 2 },
  // 中文字符串当对象键:obj["中文"]
  { name: "bracket-key", re: /\[\s*(["'`])((?:(?!\1).)+)\1\s*\]/g, group: 2 },
];

/**
 * 显式豁免标记 —— 行内含此注释则跳过(codemod 后人工确认非协议值时使用)。
 * 与 `// i18n-wrap-ok` 对照:那个表示「这行确实该包」。
 */
const EXEMPT_MARKER = "i18n-logic-value"

/**
 * 漏译检测 —— 找出**既没被 tr() 包、又不在 A 类豁免、`file:line` 也不在
 * codemod 已处理集**的中文串。这些是 en 模式下会漏中文的残留。
 *
 * 为什么需要:`i18n-codemod` 有两类**刻意不处理**的串 ——
 *   - `data-literal`:模块级数据字面量(`const LABEL = "重连"`),渲染点没包 tr
 *   - `pure-fn`:`return "窗口已开"` 这类纯函数返回,函数体内无 hook
 * 它们不是 bug(工具没错),但**是漏译**。逐批收尾时须按本清单核对。
 *
 * 判据(保守,只报高置信残留):
 *   - 行含中文,且行内**没有** `tr(` 调用
 *   - 不是纯注释行
 *   - 不在 A 类豁免行
 *   - 是字符串字面量或 JSX 文本(不是标识符/注释里的中文)
 */
/**
 * 漏译扫描的**按设计排除**清单 —— 这些文件的中文是「翻译源/键」或
 * 「渲染点翻译的数据源」,不是漏译:
 *   - `lib/i18n/**`       词表本身(中文是键)与逻辑值表
 *   - `lib/page-manifest.ts` / `lib/navigation-chains.ts` / `lib/indicators.ts`
 *                         数据文件,渲染点用 tr() 查表,文件本身**刻意不动**
 *   - `*.test.*`          测试断言中文(裸渲染走源串降级)
 */
const LEAK_EXCLUDE_RE = new RegExp(
  [
    String.raw`apps/desktop/lib/i18n/`,
    String.raw`apps/desktop/lib/page-manifest\.ts$`,
    String.raw`apps/desktop/lib/navigation-chains\.ts$`,
    String.raw`apps/desktop/lib/indicators\.ts$`,
    String.raw`apps/desktop/scripts/`,
    String.raw`\.(test|spec)\.(ts|tsx)$`,
  ].join("|"),
)

function scanLeaks(file, exempt, handled) {
  let src;
  try {
    src = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const rel = path.relative(REPO_ROOT, file).replace(/\\/g, "/");
  if (LEAK_EXCLUDE_RE.test(rel)) return [];
  const lines = src.split(/\r?\n/);
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!CJK.test(line)) continue;
    if (/^(\/\/|\*|\/\*)/.test(trimmed)) continue;
    if (line.includes(EXEMPT_MARKER)) continue;
    if (exempt.has(`${rel}:${i + 1}`)) continue;
    if (handled.has(`${rel}:${i + 1}`)) continue;
    // 行内已有 tr( → 认为已处理
    if (/\btr\(/.test(line)) continue;
    // 只认字符串字面量 / JSX 文本(排除中文标识符、字符串里纯中文注释式)
    const hasLiteral = /(["'`])[^"'`]*[一-鿿]/.test(line) || />[^<]*[一-鿿]/.test(line);
    if (!hasLiteral) continue;
    out.push({ file: rel, line: i + 1, text: trimmed.slice(0, 110) });
  }
  return out;
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
    else if (SCAN_EXT.test(e.name) && !SKIP_FILE_RE.test(e.name)) out.push(full);
  }
  return out;
}

/**
 * 展示串模式 —— 中文出现在**展示位置**但形式特殊,codemod 不能按常规
 * JSX 文本/属性值处理,须单独人工或半自动处理:
 *   return / 三元 / toast / aria / title
 * 这些**不是**危险值,但也不是普通 JSX 文本(函数体里没有 hook 可用)。
 */
const DISPLAY_PATTERNS = [
  { name: "return-literal", re: /\breturn\s+(["'`])((?:(?!\1).)+)\1/g, group: 2 },
  { name: "return-template", re: /\breturn\s+`([^`]*)`/g, group: 1 },
  { name: "ternary-branch", re: /\?\s*(["'`])((?:(?!\1).)+)\1\s*:/g, group: 2 },
  { name: "toast-call", re: /\b(toast[A-Za-z]*|alert|confirm)\s*\(\s*(["'`])((?:(?!\2).)+)\2/g, group: 3 },
  { name: "aria-title", re: /\b(aria-label|title|alt|placeholder)\s*=\s*(["'`])((?:(?!\2).)+)\2/g, group: 3 },
];

/**
 * 逐行扫描,返回命中项。
 *
 * ⚠️ 必须遍历**同一行内的所有匹配**,不能只看第一个 —— 形如
 * `if (eventSub === "signal") return e.kind === "信号"` 的行,首个匹配的
 * 操作数是 ASCII 的 `"signal"`,只取首个会漏掉真正的逻辑值 `"信号"`。
 */
function scanFile(file) {
  const hits = [];
  let src;
  try {
    src = fs.readFileSync(file, "utf8");
  } catch {
    return hits;
  }
  const lines = src.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.includes(EXEMPT_MARKER)) continue;
    // 纯注释行不含运行时代码,跳过(中文注释量大,否则淹没信号)
    const trimmed = line.trim();
    if (/^(\/\/|\*|\/\*)/.test(trimmed)) continue;
    if (!CJK.test(line)) continue;

    // 逻辑值优先:一行同时含逻辑值与展示串时,标为逻辑值(更危险)
    const logic = matchFirst(line, LOGIC_PATTERNS);
    if (logic) {
      hits.push({ ...logic, line: i + 1, text: trimmed, class: "logic-value" });
      continue;
    }
    const display = matchFirst(line, DISPLAY_PATTERNS);
    if (display) {
      hits.push({ ...display, line: i + 1, text: trimmed, class: "display-site" });
    }
  }
  return hits.map((h) => ({
    file: path.relative(REPO_ROOT, file).replace(/\\/g, "/"),
    ...h,
  }));
}

/** 在若干模式中找**首个含中文**的匹配;返回统一形状或 null。 */
function matchFirst(line, patterns) {
  for (const { name, re, group } of patterns) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(line)) !== null) {
      const value = m[group];
      if (!value || !CJK.test(value)) continue;
      return { column: m.index + 1, kind: name, value };
    }
  }
  return null;
}

function main() {
  const args = process.argv.slice(2);
  const asJson = args.includes("--json");
  const check = args.includes("--check");
  const leaksMode = args.includes("--leaks");
  const dirArg = args.indexOf("--dir");
  const target = dirArg >= 0 ? path.resolve(args[dirArg + 1]) : DEFAULT_DIR;

  const files = walk(target);
  const all = files.flatMap(scanFile);
  const logicHits = all.filter((h) => h.class === "logic-value");
  const displayHits = all.filter((h) => h.class === "display-site");

  // --leaks:列出仍会漏中文的残留(供逐批收尾核对)
  if (leaksMode) {
    const exempt = new Set(logicHits.map((h) => `${h.file}:${h.line}`));
    const handled = new Set();
    // 已由 codemod 处理的 file:line(若存在记录文件)
    try {
      const cm = JSON.parse(
        fs.readFileSync(path.join(APP_ROOT, "scripts", ".i18n-handled.json"), "utf8"),
      );
      for (const e of cm.handled ?? []) handled.add(`${e.file}:${e.line}`);
    } catch {
      // 无记录 = 全部视为未处理
    }
    const leaks = files.flatMap((f) => scanLeaks(f, exempt, handled));
    if (asJson) {
      process.stdout.write(JSON.stringify({ leakLines: leaks.length, leaks }, null, 2) + "\n");
    } else {
      console.log(`扫描 ${files.length} 个文件,发现 ${leaks.length} 行疑似漏译残留(无 tr 包裹)。\n`);
      const byFile = new Map();
      for (const l of leaks) {
        if (!byFile.has(l.file)) byFile.set(l.file, []);
        byFile.get(l.file).push(l);
      }
      for (const [f, list] of [...byFile.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 30)) {
        console.log(`  ${f}  ${list.length} 行`);
        for (const l of list.slice(0, 3)) console.log(`      :${l.line}  ${l.text}`);
      }
      console.log(`\n合计 ${byFile.size} 文件。这些是 data-literal / pure-fn 类残留,须逐处渲染点包裹。`);
    }
    return;
  }

  // 按唯一值聚合 —— 豁免清单是「值」级(同一协议值可能出现在多处)
  const groupByValue = (list) => {
    const byValue = new Map();
    for (const h of list) {
      if (!byValue.has(h.value)) byValue.set(h.value, []);
      byValue.get(h.value).push(h);
    }
    return [...byValue.entries()].sort((a, b) => b[1].length - a[1].length);
  };
  const logicByValue = groupByValue(logicHits);
  const displayByValue = groupByValue(displayHits);

  if (asJson) {
    process.stdout.write(
      JSON.stringify(
        {
          scannedFiles: files.length,
          logicValueLines: logicHits.length,
          logicValueUnique: logicByValue.length,
          displaySiteLines: displayHits.length,
          displaySiteUnique: displayByValue.length,
          // 行级豁免清单(codemod 消费) —— 按 file:line 匹配,勿按值匹配
          exemptLines: logicHits.map((h) => ({
            file: h.file,
            line: h.line,
            value: h.value,
            kind: h.kind,
          })),
          logicValues: logicByValue.map(([value, list]) => ({
            value,
            count: list.length,
            occurrences: list.map((h) => ({ file: h.file, line: h.line, kind: h.kind })),
          })),
          displaySites: displayByValue.map(([value, list]) => ({
            value,
            count: list.length,
            occurrences: list.map((h) => ({ file: h.file, line: h.line, kind: h.kind })),
          })),
          hits: all,
        },
        null,
        2,
      ) + "\n",
    );
    return;
  }

  console.log(`扫描 ${files.length} 个文件。\n`);

  console.log(`━━ A 类:逻辑值(DO-NOT-WRAP)━━ ${logicHits.length} 行 / ${logicByValue.length} 唯一值`);
  for (const [value, list] of logicByValue) {
    console.log(`  "${value}"  ×${list.length}`);
    for (const h of list.slice(0, 4)) console.log(`      ${h.file}:${h.line}  [${h.kind}]`);
    if (list.length > 4) console.log(`      … 另 ${list.length - 4} 处`);
  }
  console.log(
    "\n⚠️ A 类中文串出现在逻辑位置,**不得**包 `tr()`。展示点改用 lib/i18n/values.ts 映射。",
  );
  console.log("   确认某个值其实是展示文案 → 在该行加 `// i18n-logic-value` 反标记(此脚本会跳过)。");

  console.log(`\n━━ B 类:特殊展示位(须调用点包裹,纯函数内无 hook)━━ ${displayHits.length} 行 / ${displayByValue.length} 唯一值`);
  for (const [value, list] of displayByValue.slice(0, 25)) {
    console.log(`  "${value}"  ×${list.length}`);
    for (const h of list.slice(0, 3)) console.log(`      ${h.file}:${h.line}  [${h.kind}]`);
  }
  if (displayByValue.length > 25) console.log(`  … 另 ${displayByValue.length - 25} 个值`);
  console.log(
    "\nℹ️ B 类是**展示文案**,但出现在函数体 return / 三元 / toast / aria 位,组件内无 hook 上下文。",
  );
  console.log("   codemod 不自动包;改在**调用点**用 tr() 包裹,或把 tr 作为参数传入纯函数。");

  if (check && logicHits.length > 0) {
    console.error(`\n--check 失败:发现 ${logicHits.length} 处未复核的逻辑值。`);
    process.exitCode = 1;
  }
}

main();
