// R7 品牌抬头验证(禁 screenshot,DOM 断言 only)
// 用法: node verify-brandbar.mjs  (需 next start -p 3013 已运行)
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const BASE = "http://localhost:3013";
const AXE_SRC = readFileSync(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");
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

// BB-1: header 存在且在页面最顶部
const bar = await page.evaluate(() => {
  const h = document.querySelector(".landing-root > header");
  if (!h) return null;
  const r = h.getBoundingClientRect();
  return { top: Math.round(r.top), height: Math.round(r.height), first: document.querySelector(".landing-root")?.firstElementChild?.tagName };
});
check("BB-1 抬头在页顶且为首元素", bar && bar.top === 0 && bar.height > 0 && bar.first === "HEADER", JSON.stringify(bar));

// BB-2: 页眉文本含 SeleneQuant(词标在屏;2026-10-07 品牌换证)
const wordmark = await page.evaluate(
  () => {
    const t = document.querySelector(".landing-root > header")?.innerText ?? "";
    return t.includes("Selene") && t.includes("Quant");
  },
);
check("BB-2 词标 SeleneQuant 在抬头显示", wordmark);

// BB-3: 官方轨道新月徽(原图 PNG,非手绘重描)· 需可见且 URL 200
const mark = await page.evaluate(async () => {
  const img = document.querySelector(".landing-root > header img");
  if (!img) return null;
  const r = img.getBoundingClientRect();
  let ok = false;
  try { ok = (await fetch(img.currentSrc || img.src)).ok; } catch { ok = false; }
  return { src: img.getAttribute("src"), w: Math.round(r.width), h: Math.round(r.height), ok };
});
check(
  "BB-3 轨道新月徽(原图 PNG,可见且 200)",
  mark && mark.ok && mark.w >= 20 && mark.h >= 20,
  JSON.stringify(mark),
);

// BB-4: 抬头无导航链接(设计决定:导航在右侧 ProgressRail,抬头只有 1 个站内锚 + 1 个外链)
const links = await page.evaluate(() => {
  const hs = document.querySelector(".landing-root > header");
  return [...hs.querySelectorAll("a")].map((a) => a.getAttribute("href"));
});
const internal = links.filter((h) => h?.startsWith("#"));
const external = links.filter((h) => h?.startsWith("http"));
check(
  "BB-4 抬头无 SaaS 导航(站内锚=1 外链=1)",
  links.length === 2 && internal.length === 1 && external.length === 1,
  `links=${JSON.stringify(links)}`,
);

// BB-5: 真读数在场(40 问 / 13 证据,来自 SCALE_FACTS,非编造)
const facts = await page.evaluate(() => {
  const t = document.querySelector(".landing-root > header")?.innerText || "";
  return { q: t.includes("40"), e: t.includes("13"), unitQ: t.includes("问"), unitE: t.includes("证据") };
});
check("BB-5 文档戳真读数(40 问 · 13 证据)", facts.q && facts.e && facts.unitQ && facts.unitE, JSON.stringify(facts));

// BB-6: GitHub 外链指向真实仓库 selenequant
const repo = await page.evaluate(
  () => [...document.querySelectorAll(".landing-root > header a")].some((a) => a.href.includes("github.com/papisoV/selenequant")),
);
check("BB-6 GitHub 链接 = papisoV/selenequant", repo);

// BB-7: favicon 生效(link rel=icon 指向 icon.svg,且该 URL 200)
const iconHref = await page.evaluate(() => {
  const l = [...document.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]')];
  return l.map((x) => x.getAttribute("href"));
});
const iconOk = await page.evaluate(async () => {
  const l = document.querySelector('link[rel="icon"]');
  if (!l) return false;
  const url = l.href;
  try { const r = await fetch(url); return r.ok; } catch { return false; }
});
check("BB-7 favicon icon.svg 生效", iconOk, JSON.stringify(iconHref));

// BB-8: 滚动后抬头仍可见(sticky)
await page.evaluate(() => window.scrollTo(0, 2500));
await page.waitForTimeout(500);
const sticky = await page.evaluate(() => {
  const h = document.querySelector(".landing-root > header");
  const r = h.getBoundingClientRect();
  return { top: Math.round(r.top), visible: r.top >= -1 && r.top < 40 };
});
check("BB-8 滚动后抬头粘滞在顶", sticky.visible, JSON.stringify(sticky));

// BB-9: 无横向溢出(375/768/1440)
for (const w of [375, 768, 1440]) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.waitForTimeout(400);
  const over = await page.evaluate(() => {
    const d = document.documentElement;
    return d.scrollWidth > d.clientWidth + 1 ? `doc ${d.scrollWidth}>${d.clientWidth}` : "";
  });
  check(`BB-9 overflow=0 @${w}`, over === "", over || "clean");
}
await page.setViewportSize({ width: 1440, height: 900 });

// BB-10: 抬头不遮挡 hero 内容(hero 首个文本元素在抬头下)
// ⚠️ 页面开了 scroll-behavior:smooth,必须用 behavior:"instant" 回顶,
//    否则 window.scrollTo(0,0) 是动画,测量时页面还在中途 → 假 FAIL。
await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
await page.waitForTimeout(400);
const notObscured = await page.evaluate(() => {
  const h = document.querySelector(".landing-root > header").getBoundingClientRect();
  const heroKicker = document.querySelector("#hero p");
  if (!heroKicker) return null;
  const r = heroKicker.getBoundingClientRect();
  return r.top >= h.bottom - 2;
});
check("BB-10 抬头不遮 hero 首行", notObscured === true, String(notObscured));

// BB-11: 对比度(词标 ink on bg;文档戳 ink-3 on bg)
const contrast = await page.evaluate(() => {
  const lum = (c) => {
    const [r, g, b] = c.match(/\d+/g).slice(0, 3).map(Number).map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const bg = getComputedStyle(document.querySelector(".landing-root")).backgroundColor;
  const wordEl = document.querySelector(".landing-root > header span.text-ink") || document.querySelector(".landing-root > header a span");
  const stampEl = document.querySelector(".landing-root > header .text-ink-3");
  return {
    bg,
    word: wordEl ? +ratio(getComputedStyle(wordEl).color, bg).toFixed(2) : null,
    stamp: stampEl ? +ratio(getComputedStyle(stampEl).color, bg).toFixed(2) : null,
  };
});
check(
  "BB-11 对比度 词标>=4.5 戳>=4.5",
  contrast.word >= 4.5 && contrast.stamp >= 4.5,
  JSON.stringify(contrast),
);

// BB-12: axe 无违规(从本地 node_modules 注入;CDN 断网会假崩,见 feedback_green-tests-may-depend-on-live-network)
try {
  await page.addScriptTag({ content: AXE_SRC });
  const axe = await page.evaluate(async () => {
    const r = await window.axe.run(document.querySelector(".landing-root > header"), { resultTypes: ["violations"] });
    return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target[0]).join(",")}`);
  });
  check("BB-12 axe 抬头零违规", axe.length === 0, axe.join(" | ") || "clean");
} catch (e) {
  check("BB-12 axe 抬头零违规", false, `axe failed: ${String(e).slice(0, 80)}`);
}

check("BB-13 console 0 error", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ") || "clean");

await browser.close();
const fails = results.filter((r) => !r.pass);
console.log(`\n== ${results.length - fails.length}/${results.length} PASS ==`);
process.exit(fails.length > 0 ? 1 : 0);
