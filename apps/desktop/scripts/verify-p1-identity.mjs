// P1 身份层验证 —— 实测 landing 作用域 token 是否生效 + 改名落地 + 对比度。
// 用法: node verify-p1-identity.mjs  (需 next dev/start -p 3013 已运行)
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

// ratio helper injected into page
const token = await page.evaluate(() => {
  const root = document.querySelector(".landing-root");
  if (!root) return null;
  const cs = getComputedStyle(root);
  return {
    primary: cs.getPropertyValue("--color-primary").trim(),
    gold: cs.getPropertyValue("--color-gold").trim(),
    pfg: cs.getPropertyValue("--color-primary-foreground").trim(),
    bg: cs.getPropertyValue("--color-bg").trim(),
  };
});
check("P1-1 landing 作用域 primary 已改暖桃", token?.primary === "#f7a07d", JSON.stringify(token));
check("P1-2 landing 作用域 gold 随迁", token?.gold === "#f7a07d", token?.gold || "");

// 计算元素实际渲染色 + 对比度
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
  const root = document.querySelector(".landing-root");
  const bg = getComputedStyle(root).backgroundColor;
  // 身份色用于文字的两处:词标 "Quant" + hero QUESTION 标签
  const quant = [...document.querySelectorAll(".landing-root header span")].find((s) => s.textContent?.trim() === "Quant");
  // R7/P3:hero 的 QUESTION 标签由内联 style({color: n.hue=--color-primary})着色,不再是 .text-primary;
  // 窄屏图例的 step 文本带 inline color,取它核对对比度。
  const qLabelEl = [...document.querySelectorAll("#hero span")].find((s) => s.textContent?.trim() === "QUESTION" && s.style.color);
  const btn = document.querySelector(".landing-root a[href='/research/today']");
  return {
    bg,
    quantColor: quant ? getComputedStyle(quant).color : null,
    quantRatio: quant ? +ratio(getComputedStyle(quant).color, bg).toFixed(2) : null,
    qColor: qLabelEl ? getComputedStyle(qLabelEl).color : null,
    qRatio: qLabelEl ? +ratio(getComputedStyle(qLabelEl).color, bg).toFixed(2) : null,
    btnBg: btn ? getComputedStyle(btn).backgroundColor : null,
    btnFg: btn ? getComputedStyle(btn).color : null,
    btnRatio: btn ? +ratio(getComputedStyle(btn).color, getComputedStyle(btn).backgroundColor).toFixed(2) : null,
  };
});
check("P1-3 词标 Quant 对比度 >=4.5", contrast.quantRatio >= 4.5, `${contrast.quantRatio}:1 (${contrast.quantColor})`);
check("P1-4 hero QUESTION 标签对比度 >=4.5", contrast.qRatio >= 4.5, `${contrast.qRatio}:1 (${contrast.qColor})`);
check("P1-5 CTA 按钮文字对比度 >=4.5", contrast.btnRatio >= 4.5, `${contrast.btnRatio}:1 fg=${contrast.btnFg} bg=${contrast.btnBg}`);

// 改名
const naming = await page.evaluate(() => ({
  title: document.title,
  headerText: document.querySelector(".landing-root > header")?.innerText || "",
  footerText: document.querySelector(".landing-root footer")?.innerText || "",
  oldName: document.body.innerText.includes("SeleneSpace"),
}));
check("P1-6 文档 title = SeleneQuant", naming.title === "SeleneQuant", naming.title);
check("P1-7 抬头含 SeleneQuant 词标", naming.headerText.includes("Selene") && naming.headerText.includes("Quant"), "");
check("P1-8 页脚含 SeleneQuant", naming.footerText.includes("SeleneQuant"), "");
check("P1-9 全页无残留 SeleneSpace", !naming.oldName, "");

// favicon(2026-10-07 起为 icon.png 原图印记,非文本 SVG —— 断言只查存在+200)
const fav = await page.evaluate(async () => {
  const l = document.querySelector('link[rel="icon"]');
  if (!l) return { href: null, ok: false };
  try {
    const r = await fetch(l.href);
    return { href: l.href, ok: r.ok };
  } catch { return { href: l.href, ok: false }; }
});
check("P1-10 favicon 生效(存在且 200)", fav.ok, fav.href || "");

check("P1-11 console 0 error", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ") || "clean");

await browser.close();
const fails = results.filter((r) => !r.pass);
console.log(`\n== ${results.length - fails.length}/${results.length} PASS ==`);
process.exit(fails.length > 0 ? 1 : 0);
