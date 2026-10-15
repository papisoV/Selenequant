// P5 动效基线探针(禁 screenshot,DOM/Web-Animations 断言 only)
// 用法: node verify-motion.mjs   (需 next start -p 3013 已运行)
//
// 查两件事:
//  1. CSS 动画/过渡:在 reducedMotion=reduce 下仍 running 的 = 漏网
//  2. SMIL(<animate>/<animateMotion>/<animateTransform>):CSS media query 管不到它,
//     必须靠类(display:none)或 JS 门控 → 逐个判是否被有效抑制
import { chromium } from "playwright";

const BASE = "http://localhost:3013";
const results = [];
const check = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? " — " + detail : ""}`);
};

const browser = await chromium.launch();

// ── 1. reduced-motion 上下文 ─────────────────────────────────────────────
const rmCtx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  reducedMotion: "reduce",
});
const rm = await rmCtx.newPage();
await rm.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
await rm.waitForTimeout(1200);

// 滚到底,触发所有 IO/进场,让该静止的暴露
await rm.evaluate(async () => {
  const step = Math.round(window.innerHeight * 0.8);
  for (let y = 0; y < document.body.scrollHeight; y += step) {
    window.scrollTo({ top: y, behavior: "instant" });
    await new Promise((r) => setTimeout(r, 60));
  }
  window.scrollTo({ top: 0, behavior: "instant" });
});
await rm.waitForTimeout(600);

const cssRunning = await rm.evaluate(() => {
  const running = document.getAnimations().filter((a) => a.playState === "running");
  return running.map((a) => {
    const t = a.effect?.target;
    const cls = t?.getAttribute?.("class") || t?.tagName || "?";
    return `${a.constructor.name}:${a.animationName || a.transitionProperty || "?"}@${String(cls).slice(0, 48)}`;
  });
});
check(
  "P5-1 reduced-motion 下 CSS 动画/过渡全停(0 running)",
  cssRunning.length === 0,
  cssRunning.length ? cssRunning.join(" | ") : "clean",
);

// ── 2. SMIL 抑制核查(CSS media query 覆盖不到) ──────────────────────────
const smil = await rm.evaluate(() => {
  const root = document.querySelector(".landing-root");
  if (!root) return { total: -1, unsuppressed: [] };
  const els = [...root.querySelectorAll("animate, animateMotion, animateTransform")];
  const visible = (el) => {
    // 沿祖先链看是否被 display:none / visibility:hidden 藏掉(SMIL 随宿主隐藏即停)
    let n = el;
    while (n && n !== document.documentElement) {
      const s = getComputedStyle(n);
      if (s.display === "none" || s.visibility === "hidden") return false;
      n = n.parentElement;
    }
    return true;
  };
  const unsuppressed = els
    .filter((el) => visible(el))
    .map((el) => {
      const host = el.closest("svg")?.getAttribute("class") || el.parentElement?.tagName || "?";
      const attr = el.getAttribute("attributeName") || el.tagName;
      return `${el.tagName}/${attr}@${String(host).slice(0, 40)}`;
    });
  return { total: els.length, unsuppressed };
});
check(
  "P5-2 reduced-motion 下 SMIL 全被抑制(隐藏宿主)",
  smil.unsuppressed.length === 0,
  `total=${smil.total} unsuppressed=[${smil.unsuppressed.join(", ")}]`,
);

// ── 3. 正文/关键内容在 reduced-motion 下仍完整可见 ───────────────────────
const contentOk = await rm.evaluate(() => {
  const root = document.querySelector(".landing-root");
  const hidden = [...root.querySelectorAll("[style*='opacity: 0'], [style*='opacity:0']")].length;
  const text = (root.innerText || "").length;
  return { hidden, text };
});
check(
  "P5-3 reduced-motion 下内容全可见(opacity:0=0 且正文在)",
  contentOk.hidden === 0 && contentOk.text > 4000,
  `hidden=${contentOk.hidden} textLen=${contentOk.text}`,
);

// ── 4. 默认(可动效)下,动效确实在跑(防空转) ─────────────────────────────
const noPref = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  reducedMotion: "no-preference",
});
const nm = await noPref.newPage();
await nm.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
await nm.waitForTimeout(1500);
const mA = await nm.evaluate(() => {
  const running = document.getAnimations().filter((a) => a.playState === "running");
  const smilCount = document.querySelectorAll(".landing-root animate, .landing-root animateMotion").length;
  return { css: running.length, smil: smilCount };
});
check(
  "P5-4 默认可动效下有动画在跑(CSS>0 且 SMIL>0)",
  mA.css > 0 && mA.smil > 0,
  `cssRunning=${mA.css} smil=${mA.smil}`,
);

await browser.close();
const fails = results.filter((r) => !r.pass);
console.log(`\n== ${results.length - fails.length}/${results.length} PASS ==`);
process.exit(fails.length > 0 ? 1 : 0);
