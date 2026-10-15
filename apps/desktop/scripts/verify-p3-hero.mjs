// P3 Hero 轨道重做验证 —— 结构 + 几何 + 动效纪律 + 尺寸。
// 用法: node verify-p3-hero.mjs  (需 next dev/start -p 3013 已运行)
import { chromium } from "playwright";

const BASE = "http://localhost:3013";
const results = [];
const check = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? " — " + detail : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(1000);

// P3-1 轨道 SVG 存在
const svg = await page.evaluate(() => {
  const s = document.querySelector("#hero .lp-hero-orbit");
  return s ? { has: true, vb: s.getAttribute("viewBox") } : null;
});
check("P3-1 轨道 SVG 存在", svg?.has, JSON.stringify(svg));

// P3-2 六节点(六个 step 标签在)
const nodes = await page.evaluate(() => {
  const svg = document.querySelector("#hero .lp-hero-orbit");
  const texts = [...svg.querySelectorAll("text")].map((t) => t.textContent);
  const steps = ["QUESTION", "EXPERIMENT", "EVIDENCE", "DECISION", "OUTCOME", "MEMORY"];
  return steps.filter((s) => texts.includes(s));
});
check("P3-2 六个闭环节点齐全", nodes.length === 6, nodes.join(","));

// P3-3 真 id 在环上(node 标签带真 id)
const ids = await page.evaluate(() => {
  const svg = document.querySelector("#hero .lp-hero-orbit");
  const texts = [...svg.querySelectorAll("text")].map((t) => t.textContent);
  return ["q-024cfb997e06", "rev-c0515ec80093"].filter((id) => texts.includes(id));
});
check("P3-3 真 id 可回源标签在", ids.length === 2, ids.join(","));

// P3-4 轨道几何 = 倾斜椭圆(有 rotate 组 + arc path + mpath)
const geo = await page.evaluate(() => {
  const svg = document.querySelector("#hero .lp-hero-orbit");
  const rot = svg.querySelector("g[transform^='rotate']");
  const arc = svg.querySelector("path[id='lp-orbit-path']");
  const mpath = svg.querySelector("mpath");
  return {
    rot: rot?.getAttribute("transform") || null,
    arc: arc?.getAttribute("d")?.slice(0, 30) || null,
    mpath: !!mpath,
  };
});
check("P3-4 倾斜椭圆轨道(rotate+arc+mpath)", geo.rot && geo.arc && geo.mpath, JSON.stringify(geo));

// P3-5 中心星芒 + 公转光点
const deco = await page.evaluate(() => {
  const svg = document.querySelector("#hero .lp-hero-orbit");
  return {
    star: !!svg.querySelector("path.lp-orbit-star"),
    comets: svg.querySelectorAll(".lp-orbit-comet").length,
    grad: !!svg.querySelector("#lp-orbit-grad"),
  };
});
check("P3-5 星芒+光点×2+轨道渐变", deco.star && deco.comets === 2 && deco.grad, JSON.stringify(deco));

// P3-6 用途色走身份桃(token,非硬编码旧金)
const peach = await page.evaluate(() => {
  const svg = document.querySelector("#hero .lp-hero-orbit");
  const circles = [...svg.querySelectorAll("circle")].map((c) => c.getAttribute("fill"));
  return circles.some((f) => f === "var(--color-primary)");
});
check("P3-6 起点节点用身份色 token", peach, "");

// P3-7 旧"细线光点"已退场
const oldGone = await page.evaluate(() => ({
  rail: !!document.querySelector(".lp-loop-rail"),
  dot: !!document.querySelector(".lp-loop-dot"),
}));
check("P3-7 旧 rail/光点已退场", !oldGone.rail && !oldGone.dot, JSON.stringify(oldGone));

// P3-8 窄屏(<768)环上标签隐藏、图例补位
await page.setViewportSize({ width: 375, height: 812 });
await page.waitForTimeout(500);
const mobile = await page.evaluate(() => {
  const label = document.querySelector("#hero .lp-orbit-label");
  const legend = document.querySelector("#hero .md\\:hidden");
  return {
    labelHidden: label ? getComputedStyle(label).display === "none" : false,
    legendVisible: legend ? getComputedStyle(legend).display !== "none" : false,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };
});
check("P3-8 375px: 环标签藏 + 图例现 + 零溢出", mobile.labelHidden && mobile.legendVisible && !mobile.overflow, JSON.stringify(mobile));

// P3-9 桌面无横向溢出
await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(400);
const desk = await page.evaluate(() => ({
  overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
}));
check("P3-9 1440px 零横向溢出", !desk.overflow, "");

// P3-10 reduced-motion:光点隐藏,轨道仍在
const p2 = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await p2.emulateMedia({ reducedMotion: "reduce" });
await p2.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
await p2.waitForTimeout(700);
const rm = await p2.evaluate(() => {
  const comet = document.querySelector("#hero .lp-orbit-comet");
  const line = document.querySelector("#hero .lp-orbit-line");
  return {
    cometHidden: comet ? getComputedStyle(comet).display === "none" : false,
    lineVisible: line ? getComputedStyle(line).display !== "none" : false,
  };
});
await p2.close();
check("P3-10 reduced-motion: 光点隐/轨道在", rm.cometHidden && rm.lineVisible, JSON.stringify(rm));

check("P3-11 console 0 error", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ") || "clean");

await browser.close();
const fails = results.filter((r) => !r.pass);
console.log(`\n== ${results.length - fails.length}/${results.length} PASS ==`);
process.exit(fails.length > 0 ? 1 : 0);
