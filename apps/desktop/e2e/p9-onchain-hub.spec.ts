import { test, expect } from "@playwright/test";
import { mockHealth } from "./mock-health";

// 必须用 localhost 而非 127.0.0.1:Next 16 dev 默认 allowedDevOrigins 只放行 localhost
const BASE = "http://localhost:3000";

test.describe("P9 链上工作台(r5 拼装)", () => {
  test("1. /watch/onchain-hub 渲染 block+2tab(链上总览/链上指标 seg/鲸鱼钱流 seg)", async ({ page }) => {
    await mockHealth(page);
    const resp = await page.goto(`${BASE}/watch/onchain-hub`);
    expect(resp?.status()).toBe(200);
    await page.waitForLoadState("networkidle");

    // r5 拼装结构:2 tab + met tab 内 seg 视图按钮(总览/活跃地址/NVT/SOPR)
    await expect(page.getByRole("tab", { name: "链上指标" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "鲸鱼钱流" })).toBeVisible();
    await expect(page.getByRole("button", { name: "活跃地址" })).toBeVisible();
    await expect(page.getByRole("button", { name: "SOPR" })).toBeVisible();

    // whale tab 切过去:巨鲸转账/KOL 信号 seg + demo 兜底徽章(超时放宽到 15s)
    await page.getByRole("tab", { name: "鲸鱼钱流" }).click();
    await expect(page.locator(".feed-demo-badge").first()).toBeVisible({ timeout: 15000 });

    await page.screenshot({ path: "e2e/screenshots/p9-onchain-hub.png", fullPage: true });
  });
});
