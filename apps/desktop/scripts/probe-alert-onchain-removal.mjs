// 预警规则「onchain 下架」渲染验收探针(A4 阶段 0.5)
// 用法: node scripts/probe-alert-onchain-removal.mjs <outJson> [port]
// 前置: 另一个终端已起 `npx next dev -p <port>`。
// 产出: JSON(量化指标)+ .log(逐条 PASS/FAIL)+ 截图。
import { chromium } from "playwright";
import { appendFileSync, writeFileSync } from "node:fs";
import { readFileSync } from "node:fs";

const OUT = process.argv[2] ?? "<local-path-redacted>";
const PORT = process.argv[3] ?? "3111";
const BASE = `http://127.0.0.1:${PORT}`;
const axeSource = readFileSync(
  new URL("../node_modules/axe-core/axe.min.js", import.meta.url),
  "utf-8",
);

const results = [];
const log = (m) => {
  appendFileSync(OUT + ".log", m + "\n");
  console.log(m);
};
const check = (name, ok, detail) => {
  results.push({ name, ok, detail });
  log(`${ok ? "PASS" : "FAIL"}  ${name}  ${JSON.stringify(detail)}`);
};

// 后端此刻可能返回旧的 demo 规则;规则页的列表来自 localStorage,
// 所以这里不拦后端,只往 localStorage 种一条历史 onchain 规则。
const LEGACY_RULE = {
  id: "legacy-onchain-1",
  name: "历史链上规则",
  type: "onchain",
  threshold: { metric: "sopr", min_value: 1 },
  enabled: true,
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const API_ERRORS = [];
page.on("pageerror", (e) => API_ERRORS.push(String(e)));

await page.addInitScript((rule) => {
  window.localStorage.setItem("selenespace:alerts:v1", JSON.stringify([rule]));
}, LEGACY_RULE);

const url = `${BASE}/watch/intelligence-alerts-rules`;
const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
check("页面 200", resp?.status() === 200, { status: resp?.status() });

// 该页有同步轮询(后端规则同步),networkidle 不会发生 ⇒ 等确定的选择器。
await page.waitForSelector(".alert-rules-page", { timeout: 120000 });
await page.waitForSelector(".alert-rule-card", { timeout: 60000 });

// ── 场景 1:历史 onchain 规则仍可见 + 明确标注不再支持 ──
const legacyCard = page.locator(".alert-rule-card", { hasText: "历史链上规则" });
check("历史 onchain 规则仍在列表里", (await legacyCard.count()) === 1, {
  count: await legacyCard.count(),
});
const badge = legacyCard.locator("text=(不再支持)");
const badgeVisible = await badge.isVisible().catch(() => false);
const badgeBox = badgeVisible ? await badge.boundingBox() : null;
check("带「不再支持」标注且可见", badgeVisible, {
  visible: badgeVisible,
  box: badgeBox && {
    w: Math.round(badgeBox.width),
    h: Math.round(badgeBox.height),
  },
});
// 颜色取证:标注不能是金色/涨跌色,避免被读成"重要"。
const badgeColor = await badge
  .evaluate((el) => getComputedStyle(el).color)
  .catch(() => null);
check("标注用中性 ink 色而非语义色", /rgb\(132, 136, 141\)|rgb\(136, 140, 145\)/.test(badgeColor ?? ""), {
  color: badgeColor,
});

// ── 场景 2:类型下拉只剩 4 项 ──
await page.getByRole("button", { name: "+ 新建规则" }).click();
await page.waitForSelector(".alert-rule-form", { timeout: 60000 });
const typeOptions = await page
  .getByLabel("规则类型")
  .locator("option")
  .evaluateAll((els) => els.map((e) => ({ value: e.value, text: e.textContent })));
check("规则类型枚举 = 4 项且无 onchain",
  JSON.stringify(typeOptions.map((o) => o.value)) ===
    JSON.stringify(["price", "indicator", "large_trade", "voice"]),
  { options: typeOptions },
);

// ── 场景 3:indicator 指标下拉只剩 RSI/MACD ──
await page.getByLabel("规则类型").selectOption("indicator");
await page.waitForTimeout(120);
const metricOptions = await page
  .locator(".alert-rule-form select")
  .nth(1) // 0 = 规则类型,1 = 指标
  .locator("option")
  .evaluateAll((els) => els.map((e) => e.value));
check("indicator 指标只剩 RSI/MACD(无 SOPR/NVT)",
  JSON.stringify(metricOptions) === JSON.stringify(["RSI", "MACD"]),
  { metrics: metricOptions },
);

await page.screenshot({ path: OUT.replace(/\.json$/, "") + "-form.png", fullPage: true });

// ── 场景 4:无障碍 —— 新表单单次 axe 扫描 ──
await page.addScriptTag({ content: axeSource });
const axe = await page.evaluate(async () => {
  // eslint-disable-next-line no-undef
  const r = await window.axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
  });
  return { violations: r.violations.map((v) => ({ id: v.id, nodes: v.nodes.length })) };
});
check("axe WCAG2 A/AA 零违规", axe.violations.length === 0, axe);

// 关掉弹窗,回到列表截图(localStorage 里那条历史规则是取证重点)
await page.keyboard.press("Escape");
await page.waitForTimeout(200);
await page.screenshot({ path: OUT.replace(/\.json$/, "") + "-list.png", fullPage: true });

check("无未捕获页面异常", API_ERRORS.length === 0, { errors: API_ERRORS.slice(0, 3) });

const summary = {
  ok: results.every((r) => r.ok),
  passed: results.filter((r) => r.ok).length,
  total: results.length,
  results,
};
writeFileSync(OUT, JSON.stringify(summary, null, 2), "utf8");
log(`\n${summary.passed}/${summary.total} PASS`);
await browser.close();
process.exit(summary.ok ? 0 : 1);
