import { test, expect } from "@playwright/test";
import { mockHealth } from "./mock-health";

// 必须用 localhost 而非 127.0.0.1:Next 16 dev 默认 allowedDevOrigins 只放行 localhost,
// 走 127.0.0.1 会 block _next/* dev 资源 → React 不 hydration → 页面冻死在 boot 门
const BASE = "http://localhost:3000";

test.describe("P8 favorites 自选页", () => {
  test("1. /watch/favorites 渲染列表(空态 demo 12 行)", async ({ page }) => {
    await mockHealth(page);
    const resp = await page.goto(`${BASE}/watch/favorites`);
    expect(resp?.status()).toBe(200);
    await page.waitForLoadState("networkidle");

    // 根节点 class=favorites-list 必须存在(Critic 裁决:全库零命中,本页是首个落地)
    await expect(page.locator(".favorites-list")).toBeVisible();
    // 后端代理断 → useSymbolFavorites GET 失败 → 空态 → DEFAULT_SYMBOLS demo 行
    expect(await page.locator(".favorites-list .fav-row").count()).toBeGreaterThan(0);

    await page.screenshot({ path: "e2e/screenshots/p8-favorites.png", fullPage: true });
  });
});
