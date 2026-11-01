#!/usr/bin/env node
/**
 * i18n 模板串 codemod —— 把含中文的**模板字面量**转成 `tr("…{p}…", { p })`。
 *
 * 为什么单独一个脚本:`i18n-codemod.mjs` 只处理「字符串字面量 + JSX 文本」,
 * 模板串(backtick)整类漏掉 —— 实测 watch/trade 共 60 处,en 模式下会直接
 * 残留中文(`已删除 ${id}`、`订单 #{n} 已成交` …)。
 *
 * 判定规则(关键):只有中文出现在模板的**文本片段(quasi)**里才转。
 * `${cond ? "演示" : …}` 这类「中文只在插值表达式内」**不转** —— 那是逻辑
 * 值或已经处理过的片段,转外层模板毫无意义(实测反例:
 * `startResult.includes("演示")`、`tr("PA 形态")`)。
 *
 * ⚠️ 默认 dry-run。`--write` 才改盘。需 `scripts/.i18n-risky.json`(缺则拒写)。
 *
 * 用法:
 *   node scripts/i18n-codemod-tmpl.mjs --dir components/trade
 *   node scripts/i18n-codemod-tmpl.mjs --dir components/watch --write
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

const SKIP_DIRS = new Set(["node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo", "scratch"]);
const SKIP_FILE_RE = /\.(test|spec)\.(ts|tsx|js|jsx)$/;
const CJK = /[一-鿿㐀-䶿]/;
const HOOK_IMPORT = 'import { useUiText } from "@/components/i18n/use-ui-text"';

function walk(dir, out = []) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      walk(path.join(dir, e.name), out);
    } else if (/\.(ts|tsx)$/.test(e.name) && !SKIP_FILE_RE.test(e.name)) {
      out.push(path.join(dir, e.name));
    }
  }
  return out;
}

function loadExemptLines() {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(APP_ROOT, "scripts", ".i18n-risky.json"), "utf8"));
    return new Set((data.exemptLines ?? []).map((e) => `${e.file}:${e.line}`));
  } catch {
    return null;
  }
}

function findInjectTargets(sf) {
  const targets = [];
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name && node.body) {
      if (/^[A-Z]/.test(node.name.text) || /^use[A-Z]/.test(node.name.text)) targets.push(node);
    } else if (
      ts.isVariableDeclaration(node) && node.initializer &&
      (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer)) &&
      ts.isIdentifier(node.name)
    ) {
      const name = node.name.text;
      if ((/^[A-Z]/.test(name) || /^use[A-Z]/.test(name)) && node.initializer.body) targets.push(node.initializer);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return targets;
}

/** 模板串的文本片段里是否含中文(只看 quasi,不看插值表达式)。 */
function quasiHasCJK(node) {
  if (ts.isNoSubstitutionTemplateLiteral(node)) return CJK.test(node.text);
  if (!ts.isTemplateExpression(node)) return false;
  if (CJK.test(node.head.text)) return true;
  return node.templateSpans.some((s) => CJK.test(s.literal.text));
}

/** 该模板的任一插值表达式里是否**嵌套**着另一个「quasi 含中文」的模板。 */
function hasNestedCJKTmpl(node) {
  if (!ts.isTemplateExpression(node)) return false;
  let found = false;
  for (const span of node.templateSpans) {
    const scan = (n) => {
      if (found) return;
      if (n !== span.expression && (ts.isTemplateExpression(n) || ts.isNoSubstitutionTemplateLiteral(n)) && quasiHasCJK(n)) {
        found = true;
        return;
      }
      ts.forEachChild(n, scan);
    };
    scan(span.expression);
  }
  return found;
}

const IDENT_OK = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

/** 为某个插值表达式选一个参数名:简单标识符/属性名优先,否则 p{i}。 */
function paramNameFor(expr, sf, i, used) {
  let base = null;
  if (ts.isIdentifier(expr)) base = expr.text;
  else if (ts.isPropertyAccessExpression(expr)) base = expr.name.text;
  else if (ts.isElementAccessExpression(expr) && ts.isStringLiteral(expr.argumentExpression)) base = expr.argumentExpression.text;
  if (!base || !IDENT_OK.test(base)) base = `p${i}`;
  // 与同名但不同表达式的 span 冲突时加后缀
  let name = base;
  let k = 1;
  while (used.has(name) && used.get(name) !== expr.getText(sf)) {
    name = `${base}${k++}`;
  }
  used.set(name, expr.getText(sf));
  return name;
}

/** 构造 `tr("…{p}…", { p: expr })` 的替换文本。 */
function buildCall(node, sf, used) {
  if (ts.isNoSubstitutionTemplateLiteral(node)) {
    return `tr(${JSON.stringify(node.text)})`;
  }
  const params = [];
  let src = node.head.text;
  node.templateSpans.forEach((span, i) => {
    const name = paramNameFor(span.expression, sf, i, used);
    src += `{${name}}`;
    params.push(`${name}: ${span.expression.getText(sf)}`);
    src += span.literal.text;
  });
  if (params.length === 0) return `tr(${JSON.stringify(src)})`;
  return `tr(${JSON.stringify(src)}, { ${params.join(", ")} })`;
}

function processFile(file, exempt) {
  const src = fs.readFileSync(file, "utf8");
  const rel = path.relative(REPO_ROOT, file).split(path.sep).join("/");
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;

  const targets = findInjectTargets(sf);
  const targetRanges = targets.filter((t) => t.body && ts.isBlock(t.body)).map((t) => [t.body.getStart(sf), t.body.getEnd()]);
  const inTarget = (pos) => targetRanges.some(([s, e]) => pos >= s && pos < e);

  const edits = [];
  const complex = [];
  const outside = [];

  const visit = (node) => {
    if (ts.isTemplateExpression(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      if (quasiHasCJK(node)) {
        // 已在 tr() 里的模板(如 tr(`…`))不动
        let insideTr = false;
        let cur = node;
        while (cur) {
          const par = cur.parent;
          if (!par) break;
          if (ts.isCallExpression(par) && ts.isIdentifier(par.expression) && par.expression.text === "tr") { insideTr = true; break; }
          if (ts.isStatement(par)) break;
          cur = par;
        }
        if (insideTr) { ts.forEachChild(node, visit); return; }

        if (isExemptPos(node)) { ts.forEachChild(node, visit); return; }

        if (hasNestedCJKTmpl(node)) {
          complex.push({ file: rel, line: lineOf(node.getStart(sf)), value: node.getText(sf).slice(0, 90) });
          ts.forEachChild(node, visit);
          return;
        }
        if (!inTarget(node.getStart(sf))) {
          outside.push({ file: rel, line: lineOf(node.getStart(sf)), value: node.getText(sf).slice(0, 90) });
          ts.forEachChild(node, visit);
          return;
        }
        const used = new Map();
        edits.push({
          start: node.getStart(sf),
          end: node.getEnd(),
          text: buildCall(node, sf, used),
          line: lineOf(node.getStart(sf)),
          value: node.getText(sf).slice(0, 90),
        });
        return; // 不再下钻(整个节点被替换)
      }
    }
    ts.forEachChild(node, visit);
  };

  const isExemptPos = (node) => exempt && exempt.has(`${rel}:${lineOf(node.getStart(sf))}`);

  visit(sf);
  return { rel, src, sf, edits, complex, outside, targets, hasHook: /useUiText/.test(src) };
}

function main() {
  const args = process.argv.slice(2);
  const write = args.includes("--write");
  const dirArg = args.indexOf("--dir");
  const target = dirArg >= 0 ? path.resolve(APP_ROOT, args[dirArg + 1]) : APP_ROOT;
  const exempt = loadExemptLines();
  if (write && !exempt) {
    console.error("拒绝写盘:未找到 scripts/.i18n-risky.json。先跑 i18n-scan-risky.mjs --json。");
    process.exit(2);
  }

  const files = walk(target);
  const results = files.map((f) => processFile(f, exempt));
  const withEdits = results.filter((r) => r.edits.length > 0);

  let total = 0;
  for (const r of withEdits) {
    total += r.edits.length;
    console.log(`  ${r.rel}  ${r.edits.length} 处`);
    for (const e of r.edits) console.log(`      L${e.line}  ${e.value}`);
  }
  const complex = results.flatMap((r) => r.complex);
  const outside = results.flatMap((r) => r.outside);
  console.log(`\n合计 ${withEdits.length} 文件 / ${total} 处可转。`);
  if (complex.length) {
    console.log(`\n⚠️ ${complex.length} 处含嵌套中文模板(须人工):`);
    for (const c of complex) console.log(`     ${c.file}:${c.line}  ${c.value}`);
  }
  if (outside.length) {
    console.log(`\n⚠️ ${outside.length} 处不在可注入 hook 的作用域内(须调用点处理):`);
    for (const o of outside) console.log(`     ${o.file}:${o.line}  ${o.value}`);
  }
  if (!write) { console.log("\n(dry-run:未改动任何文件。加 --write 真改。)"); return; }

  let n = 0;
  for (const r of results) {
    if (r.edits.length === 0) continue;
    let out = r.src;
    const inserts = [];
    for (const t of r.targets) {
      if (!t.body || !ts.isBlock(t.body)) continue;
      const [bs, be] = [t.body.getStart(r.sf), t.body.getEnd()];
      if (!r.edits.some((e) => e.start >= bs && e.start < be)) continue;
      if (/\bconst\s+tr\s*=/.test(out.slice(bs, be))) continue;
      const brace = out.indexOf("{", bs);
      const after = out[brace + 1];
      inserts.push({ pos: brace + 1, text: after === "\n" || after === undefined ? "\n  const tr = useUiText()" : "\n  const tr = useUiText()\n" });
    }
    if (!/from\s+["']@\/components\/i18n\/use-ui-text["']/.test(out)) {
      let lastImportEnd = 0;
      for (const stmt of r.sf.statements) if (ts.isImportDeclaration(stmt)) lastImportEnd = stmt.getEnd();
      if (lastImportEnd === 0) { console.error(`  跳过 ${r.rel}:无 import 块`); continue; }
      inserts.push({ pos: lastImportEnd, text: `\n${HOOK_IMPORT}` });
    }
    const ops = [
      ...r.edits.map((e) => ({ start: e.start, end: e.end, text: e.text })),
      ...inserts.map((i) => ({ start: i.pos, end: i.pos, text: i.text })),
    ];
    ops.sort((a, b) => b.start - a.start);
    for (const op of ops) out = out.slice(0, op.start) + op.text + out.slice(op.end);
    fs.writeFileSync(r.sf.fileName, out, "utf8");
    n++;
  }
  console.log(`\n✅ 已改 ${n} 文件 / ${total} 处模板串。`);
}

main();
