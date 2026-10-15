import { test, expect, type Page } from "@playwright/test";

// 响应式实测 — 决策教训行(flex 无 flex-wrap)在窄容器 + 长 strategy_id 下是否溢出
const PORT = process.env.E2E_PORT ?? "3011";
const BASE = `http://localhost:${PORT}`;

const LONG = "very_long_strategy_identifier_that_eats_horizontal_space_v2_final";

async function setup(page: Page, strategyId: string) {
  await page.route("**/api/v1/health", (r) => r.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }));
  await page.route("**/api/v1/agent/trading/workspace*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: '{"positions":[],"pending_orders":[],"active_strategies":[],"account_summary":{}}' }));
  await page.route("**/api/v1/agent/decision-lessons*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([
    { deployment_id: "d1", strategy_id: strategyId, symbols: ["BTCUSDT", "ETHUSDT"], entry_date: "2026-10-01", rating: "REVIEW", is_review: true, status: "filled", reflection_text: "评级读不出" },
  ]) }));
}

for (const w of [1280, 768, 420, 360]) {
  test(`无横向溢出 @ ${w}px`, async ({ page }) => {
    await setup(page, LONG);
    await page.setViewportSize({ width: w, height: 800 });
    await page.goto(`${BASE}/system/agent-trading-workspace`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=评级读不出", { timeout: 20_000 });
    await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));

    const m = await page.evaluate(() => {
      const de = document.documentElement;
      const badge = document.querySelector("span.bg-warn\\/20") as HTMLElement | null;
      const row = badge?.parentElement as HTMLElement | null;
      return {
        docScrollW: de.scrollWidth,
        docClientW: de.clientWidth,
        bodyScrollW: document.body.scrollWidth,
        badgeRight: badge ? badge.getBoundingClientRect().right : null,
        badgeScrollW: badge?.scrollWidth ?? null,
        badgeClientW: badge?.clientWidth ?? null,
        badgeWhiteSpace: badge ? getComputedStyle(badge).whiteSpace : null,
        rowScrollW: row?.scrollWidth ?? null,
        rowClientW: row?.clientWidth ?? null,
      };
    });
    console.log(`[${w}px] ${JSON.stringify(m)}`);
    // 页面级横向溢出 = 失败
    expect(m.docScrollW, `doc scrollWidth ${m.docScrollW} > clientWidth ${m.docClientW}`).toBeLessThanOrEqual(m.docClientW + 1);
  });
}
