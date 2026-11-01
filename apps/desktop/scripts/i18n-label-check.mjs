#!/usr/bin/env node
/**
 * 数据层标签覆盖检查(量具,补 i18n-vocab-check 的盲区)。
 *
 * `i18n-vocab-check.mjs` 只扫 components/ 与 lib/ 里的 `tr("…")`。但 hub 页
 * (`app/watch/mk/page.tsx` 等)把 `label: "市场总览"` 作为**数据**传给
 * BlockComposer,由后者在渲染点 `tr(label)` —— 源码里没有字面量 tr(),静态
 * 扫不到,词表缺键时 en 模式静默留中文。
 *
 * 本脚本直接把 app/ 下 `label:` / `title:` 的中文字面量拿去查词表。
 * 输出:每个缺失串 + 首次出现的 file:line。
 */
import fs from "node:fs";
import path from "node:path";

const APP = process.cwd();
const VOCAB_DIR = path.join(APP, "lib/i18n/ui-text");
const SKIP = new Set(["node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo", "scratch"]);

function decode(s) {
  return s
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\`/g, "`")
    .replace(/\\\\/g, "\\");
}

function loadVocab() {
  const dict = new Set();
  for (const f of fs.readdirSync(VOCAB_DIR)) {
    if (!f.endsWith(".ts") || f === "index.ts") continue;
    const src = fs.readFileSync(path.join(VOCAB_DIR, f), "utf8");
    const re = /^\s{2,4}(["'])((?:\\.|(?!\1).)*)\1\s*:/gm;
    let m;
    while ((m = re.exec(src))) dict.add(decode(m[2]));
  }
  return dict;
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (SKIP.has(e.name)) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else if (/\.(tsx|ts)$/.test(e.name) && !/(test|spec)\./.test(e.name)) out.push(full);
  }
  return out;
}

const dict = loadVocab();
const files = walk(path.join(APP, "app"));

const missing = new Map();
for (const file of files) {
  const src = fs.readFileSync(file, "utf8");
  // 只认带中文的 label/title 字面量 —— 英文标识符不是文案。
  const re = /\b(label|title):\s*(["'])((?:\\.|(?!\2)[\s\S])*?)\2/g;
  let m;
  while ((m = re.exec(src))) {
    const s = decode(m[3]);
    if (!/[一-鿿]/.test(s)) continue;
    if (dict.has(s)) continue;
    const line = src.slice(0, m.index).split(/\r?\n/).length;
    const rel = path.relative(APP, file).replace(/\\/g, "/");
    if (!missing.has(s)) missing.set(s, []);
    if (missing.get(s).length < 4) missing.get(s).push(`${rel}:${line}`);
  }
}

const rows = [...missing.entries()].sort((a, b) => b[1].length - a[1].length);
process.stdout.write(`app/ label|title literals missing from vocab: ${rows.length}\n\n`);
for (const [s, where] of rows) {
  process.stdout.write(`"${s}"\n    ${where.join("\n    ")}\n`);
}
