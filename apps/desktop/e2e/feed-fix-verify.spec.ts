import { test, expect } from "@playwright/test";

// 底部监控条 demo 兜底修复实测(2026-08-12)
// 前置:后端已通(代理 127.0.0.1 已修),不 mock health,走真实 boot 门
// 断言:健康后端下 feed 直进 live、不闪 demo 徽标、无 duplicate-key 警告
const BASE = "http://localhost:3000";

test.describe("底部监控条 demo 兜底修复实测", () => {
  test("健康后端下 feed 直进 live,不闪 demo 徽标,无 duplicate-key 警告", async ({ page }) => {
    const warnings: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "warning" || msg.type() === "error") {
        warnings.push(msg.text());
      }
    });

    const resp = await page.goto(`${BASE}/research/strat`, { waitUntil: "domcontentloaded" });
    expect(resp?.status()).toBe(200);

    // 等底部监控条挂载(真实 boot 门,代理已修 → 快速放行)
    await page.waitForSelector(".feed-bar", { timeout: 30000 });

    const samples: { t: number; demo: boolean; items: number; pulseOn: boolean }[] = [];
    let demoSeen = false;
    for (let i = 0; i < 16; i++) {
      const demo = (await page.locator(".feed-bar .feed-demo-badge").count()) > 0;
      const items = await page.locator(".feed-bar .feed-item").count();
      const pulseOn = (await page.locator(".feed-bar .pulse:not(.off)").count()) > 0;
      if (demo) demoSeen = true;
      samples.push({ t: i, demo, items, pulseOn });
      await page.waitForTimeout(1000);
    }

    console.log("SAMPLES:", JSON.stringify(samples));
    console.log("WARNINGS:", JSON.stringify(warnings));
    const feedText = (await page.locator(".feed-bar").innerText()).slice(0, 300);
    console.log("FEED_TEXT:", feedText);

    // 修复核心断言:健康后端下不应出现 demo 徽标
    expect(demoSeen, `demo badge appeared; samples=${JSON.stringify(samples)}`).toBe(false);
    // live 数据必须到达
    const liveSeen = samples.some((s) => s.items > 0 && !s.demo);
    expect(liveSeen, `no live items; samples=${JSON.stringify(samples)}`).toBe(true);
    // 无 duplicate-key 警告
    const dupKey = warnings.filter((w) => w.includes("same key"));
    expect(dupKey, `duplicate key warnings: ${JSON.stringify(dupKey)}`).toHaveLength(0);
  });
});
