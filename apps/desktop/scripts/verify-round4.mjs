// Round-4 验证脚本(禁 screenshot,DOM 断言 only)
// 用法: node verify-round4.mjs  (需 next start -p 3013 已运行)
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const BASE = "http://localhost:3013";
const AXE_SRC = readFileSync(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");
const results = [];
const check = (name, pass, detail = "") => {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? " — " + detail : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

// console error 收集
const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(String(e)));

await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });

// AC: 渲染后 opacity:0 内联 = 0(滚动到底让 IntersectionObserver 全部触发)
await page.evaluate(async () => {
  await new Promise((resolve) => {
    let y = 0;
    const step = () => {
      y += 600;
      window.scrollTo(0, y);
      if (y < document.body.scrollHeight) setTimeout(step, 80);
      else resolve(null);
    };
    step();
  });
});
await page.waitForTimeout(900);
const hiddenCount = await page.evaluate(
  () =>
    [...document.querySelectorAll(".landing-root [style*='opacity: 0'], .landing-root [style*='opacity:0']")]
      .length,
);
check("AC1 滚动到底后 landing 内 opacity:0 = 0", hiddenCount === 0, `count=${hiddenCount}`);
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(400);

// AC: council citedIds 全渲染(两视角 7 + 8 = 15 个 chip;chip = flex-wrap 容器里的 font-mono span)
const chipCount = await page.evaluate(
  () => document.querySelectorAll("#council .lp-cite-chips span").length,
);
check("AC14 council cited chips 全渲染(>=15)", chipCount >= 15, `count=${chipCount}`);

// AC: 调查会 h2 在 DOM(crawler 可见性同 SSR)
const h2 = await page.evaluate(() =>
  [...document.querySelectorAll("h2")].some((h) => h.textContent.includes("当场反驳自己人")),
);
check("AC3 调查会 h2 存在", h2);

// AC: ProgressRail 锚点数量 >= 8
const railLinks = await page.evaluate(
  () => document.querySelectorAll('nav[aria-label="页面章节"] a').length,
);
check("AC15 进度导轨锚点 >= 8", railLinks >= 8, `count=${railLinks}`);

// AC: rail 链接跳转目标都存在
const railTargets = await page.evaluate(() =>
  [...document.querySelectorAll('nav[aria-label="页面章节"] a')].map((a) =>
    a.getAttribute("href").slice(1),
  ),
);
const missingTargets = [];
for (const id of railTargets) {
  if (!(await page.$(`#${id}`))) missingTargets.push(id);
}
check("AC15 导轨锚点全部可跳", missingTargets.length === 0, missingTargets.join(",") || "all found");

// AC: AuditRail details 折叠态高度 <= 120px
const summaryH = await page.evaluate(() => {
  const d = document.querySelector("#audit details");
  return d ? d.getBoundingClientRect().height : -1;
});
check("AC12 AuditRail 折叠态 <= 120px", summaryH > 0 && summaryH <= 120, `h=${Math.round(summaryH)}px`);

// AC: 渲染文本无 em-dash
const emDash = await page.evaluate(() =>
  document.querySelector(".landing-root").innerText.includes("—"),
);
check("AC16 渲染 em-dash = 0", !emDash);

// AC: overflow 375 / 768 / 1440
for (const w of [375, 768, 1440]) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.waitForTimeout(400);
  const over = await page.evaluate(() => {
    const d = document.documentElement;
    const bad = [];
    // 全页级
    if (d.scrollWidth > d.clientWidth + 1) bad.push(`doc ${d.scrollWidth}>${d.clientWidth}`);
    return bad;
  });
  check(`AC16 overflow=0 @${w}`, over.length === 0, over.join("; ") || "clean");
}
await page.setViewportSize({ width: 1440, height: 900 });

// AC: axe-core 注入
await page.addScriptTag({ content: AXE_SRC });
try {
  const axeResults = await page.evaluate(async () => {
    const r = await window.axe.run(document.querySelector(".landing-root"), {
      resultTypes: ["violations"],
    });
    return r.violations.map(
      (v) =>
        `${v.id}(${v.nodes.length}): ` +
        v.nodes
          .slice(0, 4)
          .map((n) => (n.target[0] || "") + " " + (n.failureSummary || "").slice(0, 60))
          .join(" || "),
    );
  });
  check("AC16 axe violations = 0", axeResults.length === 0, axeResults.join("\n  ") || "clean");
} catch (e) {
  check("AC16 axe violations = 0", false, `axe failed: ${String(e).slice(0, 100)}`);
}

// AC: reduced-motion 下 Reveal 全可见
const ctx2 = await browser.newContext({
  reducedMotion: "reduce",
  viewport: { width: 1440, height: 900 },
});
const p2 = await ctx2.newPage();
await p2.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
await p2.waitForTimeout(600);
const rmHidden = await p2.evaluate(
  () =>
    [...document.querySelectorAll(".landing-root [style*='opacity: 0'], .landing-root [style*='opacity:0']")]
      .length,
);
check("AC17 reduced-motion 全可见", rmHidden === 0, `hidden=${rmHidden}`);
await ctx2.close();

// AC: console 0 error
check("AC16 console 0 error", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ") || "clean");

await browser.close();

const fails = results.filter((r) => !r.pass);
console.log(`\n== ${results.length - fails.length}/${results.length} PASS ==`);
process.exit(fails.length > 0 ? 1 : 0);
