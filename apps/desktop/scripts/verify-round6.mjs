// Round-6 验证脚本(禁 screenshot,DOM 断言 only)
// = verify-round4 的 13 条 + hero 专项 7 条(R6-1..R6-8) = 21 断言
// 用法: node verify-round6.mjs  (需 next start -p 3013 已运行)
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

const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(String(e)));

await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });

// ── hero 专项 6 条 ──────────────────────────────────────────────

// R6-1: hero 真问题 id 在屏(R6c = funding 链 QUESTION 节点)
const kickerId = await page.evaluate(
  () => document.querySelector("#hero")?.textContent?.includes("q-024cfb997e06") ?? false,
);
check("R6-1 hero 真问题 id 在屏", kickerId);

// R6-2: 悬浮徽章 0
const badgeCount = await page.evaluate(
  () => document.querySelectorAll("#hero .lp-float-badge").length,
);
check("R6-2 悬浮徽章 = 0", badgeCount === 0, `count=${badgeCount}`);

// R6-3: ResearchGraph 全页 0 挂载
const rgCount = await page.evaluate(
  () => document.querySelectorAll(".lp-rgraph").length,
);
check("R6-3 ResearchGraph 全页 = 0", rgCount === 0, `count=${rgCount}`);

// R6-4: 证据账本 4 行全渲染(R6c:账本在 #the-loop 区渲染,hero 只挂 1 条切片)
const ledgerRows = await page.evaluate(() => {
  const loopText =
    (document.querySelector("#the-loop")?.innerText || "") +
    (document.querySelector("#hero")?.innerText || "");
  const ids = [
    "ev-aa12269ea516",
    "ev-df9effd9f8ab",
    "ev-d6345a1ac57d",
    "ev-905370c8fcf8",
  ];
  return ids.filter((id) => loopText.includes(id.slice(0, 10))).length;
});
check("R6-4 证据账本 4 行全渲染(hero 切片+loop 区)", ledgerRows === 4, `rows=${ledgerRows}`);

// R6-5: 判定语义色(翻案=cyan → text-research;NO-GO=红 → text-neg;R6c 位置=全页)
const verdictColors = await page.evaluate(() => {
  const scope = document.querySelector(".landing-root");
  if (!scope) return { fan: null, nogo: null };
  const els = [...scope.querySelectorAll("span, div")];
  const fan = els.find((e) => e.textContent.trim() === "翻案" && e.children.length === 0);
  const nogo = els.find((e) => e.textContent.trim() === "NO-GO" && e.children.length === 0);
  return {
    fan: fan ? fan.className : null,
    nogo: nogo ? nogo.className : null,
  };
});
check(
  "R6-5 翻案=cyan / NO-GO=红",
  verdictColors.fan?.includes("text-research") && verdictColors.nogo?.includes("text-neg"),
  `fan=${verdictColors.fan?.slice(0, 40)} nogo=${verdictColors.nogo?.slice(0, 40)}`,
);

// R6-6: hero 金色元素 ≤3(bg-primary + text-gold 引用标,计 bg-primary/text-gold/bg-gold)
const goldCount = await page.evaluate(() => {
  const hero = document.querySelector("#hero");
  if (!hero) return -1;
  return [...hero.querySelectorAll("*")].filter((el) => {
    const c = el.className?.toString() || "";
    return /bg-primary(?!-foreground)|text-gold|bg-gold/.test(c);
  }).length;
});
check("R6-6 hero 金色元素 <= 3", goldCount >= 1 && goldCount <= 3, `count=${goldCount}`);

// R6-7: hero 主视觉 = 研究闭环轨道(P3 倾斜椭圆;六节点真 id + 双光点;
// 旧 R6c 的线性 rail/单光点已按 P3-7 退役,故此断言随之改锚到新轨道 DOM)
const loopSpec = await page.evaluate(() => {
  const hero = document.querySelector("#hero");
  if (!hero) return { has: false };
  const t = hero.innerText || "";
  const steps = ["QUESTION", "EXPERIMENT", "EVIDENCE", "DECISION", "OUTCOME", "MEMORY"];
  return {
    has: true,
    steps: steps.filter((s) => t.includes(s)).length,
    decId: t.includes("dec-6a48bd7d61c3"),
    questId: t.includes("q-024cfb997e06"),
    memId: t.includes("rev-c0515ec80093"),
    comet: hero.querySelectorAll(".lp-orbit-comet").length,
    rail: hero.querySelectorAll(".lp-loop-rail").length,
    dot: hero.querySelectorAll(".lp-loop-dot").length,
    saasCard: hero.querySelectorAll(".overflow-hidden.rounded-xl").length,
  };
});
check(
  "R6-7 闭环轨道六节点+双光点+真 id",
  loopSpec.has && loopSpec.steps === 6 && loopSpec.decId && loopSpec.questId &&
    loopSpec.memId && loopSpec.comet === 2 && loopSpec.rail === 0 && loopSpec.dot === 0 &&
    loopSpec.saasCard === 0,
  `steps=${loopSpec.steps} dec=${loopSpec.decId} q=${loopSpec.questId} mem=${loopSpec.memId} comet=${loopSpec.comet} rail=${loopSpec.rail} dot=${loopSpec.dot} oldCard=${loopSpec.saasCard}`,
);

// R6-8: 金色只属于 QUESTION —— hero 内所有用身份色(--color-primary)着色的元素,
// 文字必须是 QUESTION(轨道 SVG 标签 + 窄屏图例各一处);其余五步各走语义色。
// 旧断言数 `ol > li > p`,P3 重写后该结构不存在 → 改锚到 fill/color 内联属性。
const goldNodes = await page.evaluate(() => {
  const hero = document.querySelector("#hero");
  if (!hero) return { total: -1, nonQuestion: [] };
  const isPrimary = (v) => /--color-primary/.test(v || "");
  const els = [
    ...hero.querySelectorAll("svg text[fill]"),
    ...[...hero.querySelectorAll("span")].filter((s) => s.style.color && isPrimary(s.style.color)),
  ].filter((el) => isPrimary(el.getAttribute("fill")) || isPrimary(el.style.color));
  return {
    total: els.length,
    nonQuestion: els.map((e) => e.textContent.trim()).filter((x) => x !== "QUESTION"),
  };
});
check(
  "R6-8 轨道身份金=QUESTION 独占",
  goldNodes.total >= 1 && goldNodes.nonQuestion.length === 0,
  `total=${goldNodes.total} nonQuestion=[${goldNodes.nonQuestion.join(",")}]`,
);

// ── verify-round4 原 13 条(滚动后 opacity 检查前先滚到底) ───────────

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

const chipCount = await page.evaluate(
  () => document.querySelectorAll("#council .lp-cite-chips span").length,
);
check("AC14 council cited chips 全渲染(>=15)", chipCount >= 15, `count=${chipCount}`);

const h2 = await page.evaluate(() =>
  [...document.querySelectorAll("h2")].some((h) => h.textContent.includes("当场反驳自己人")),
);
check("AC3 调查会 h2 存在", h2);

const railLinks = await page.evaluate(
  () => document.querySelectorAll('nav[aria-label="页面章节"] a').length,
);
check("AC15 进度导轨锚点 >= 8", railLinks >= 8, `count=${railLinks}`);

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

const summaryH = await page.evaluate(() => {
  const d = document.querySelector("#audit details");
  return d ? d.getBoundingClientRect().height : -1;
});
check("AC12 AuditRail 折叠态 <= 120px", summaryH > 0 && summaryH <= 120, `h=${Math.round(summaryH)}px`);

const emDash = await page.evaluate(() =>
  document.querySelector(".landing-root").innerText.includes("—"),
);
check("AC16 渲染 em-dash = 0", !emDash);

for (const w of [375, 768, 1440]) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.waitForTimeout(400);
  const over = await page.evaluate(() => {
    const d = document.documentElement;
    const bad = [];
    if (d.scrollWidth > d.clientWidth + 1) bad.push(`doc ${d.scrollWidth}>${d.clientWidth}`);
    return bad;
  });
  check(`AC16 overflow=0 @${w}`, over.length === 0, over.join("; ") || "clean");
}
await page.setViewportSize({ width: 1440, height: 900 });

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

check("AC16 console 0 error", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ") || "clean");

await browser.close();

const fails = results.filter((r) => !r.pass);
console.log(`\n== ${results.length - fails.length}/${results.length} PASS ==`);
process.exit(fails.length > 0 ? 1 : 0);
