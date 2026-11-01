#!/usr/bin/env node
/**
 * 词表覆盖检查(量具)—— 找出 `tr("…")` 里中文串**不在任何词表**的情况。
 *
 * tr() 对查不到的源串原样透传 = en 模式下仍是中文。静态扫描抓不到这类,
 * 因为行内有 tr( 就被视为已处理;渲染扫描抓不到,因为没走到那个交互态。
 *
 * 输出:每个缺失串 + 首次出现的 file:line。
 */
import fs from "node:fs";
import path from "node:path";

const APP = process.cwd();
const VOCAB_DIR = path.join(APP, "lib/i18n/ui-text");
const SKIP = new Set(["node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo", "scratch"]);

/** 收集词表键:所有 `  "源串": "译文",` 行(容错单引号)。 */
function loadVocab() {
  const dict = new Set();
  for (const f of fs.readdirSync(VOCAB_DIR)) {
    if (!f.endsWith(".ts") || f === "index.ts") continue;
    const src = fs.readFileSync(path.join(VOCAB_DIR, f), "utf8");
    const re = /^\s{2,4}(["'])((?:\\.|(?!\1).)*)\1\s*:/gm;
    let m;
    while ((m = re.exec(src))) {
      // 与源码端同样解码 —— 词表里写的是 `"\n… (截断)"`,
      // 运行时键是真换行;不解码会与 tr() 实参对不上。
      dict.add(decode(m[2]));
    }
  }
  return dict;
}

/** 解码 JS 字符串字面量的转义(只需处理常见几种)。 */
function decode(s) {
  return s
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\`/g, "`")
    .replace(/\\\\/g, "\\");
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
const files = [...walk(path.join(APP, "components")), ...walk(path.join(APP, "lib"))].filter(
  (f) => !f.includes(path.join("lib", "i18n")),
);

const missing = new Map();
for (const file of files) {
  let src;
  try {
    src = fs.readFileSync(file, "utf8");
  } catch {
    continue;
  }
  if (!/\btr\(/.test(src)) continue;
  // 扫**整份源码**而非逐行:tr() 的字符串实参可以跨多行
  // (如 `tr("…\n\n…", {…})`),逐行取会把字符串截断成假缺失。
  const re = /\btr\(\s*(["'`])((?:\\.|(?!\1)[\s\S])*?)\1/g;
  let m;
  while ((m = re.exec(src))) {
    const s = decode(m[2]);
    if (!/[一-鿿]/.test(s)) continue;
    if (dict.has(s)) continue;
    const line = src.slice(0, m.index).split(/\r?\n/).length;
    if (!missing.has(s)) missing.set(s, []);
    const rel = path.relative(APP, file).replace(/\\/g, "/");
    if (missing.get(s).length < 4) missing.get(s).push(`${rel}:${line}`);
  }
}

const rows = [...missing.entries()].sort((a, b) => b[1].length - a[1].length);
process.stdout.write(`vocab keys: ${dict.size}\nmissing tr() sources: ${rows.length}\n\n`);
for (const [s, where] of rows) {
  process.stdout.write(`"${s}"\n    ${where.join("\n    ")}\n`);
}
