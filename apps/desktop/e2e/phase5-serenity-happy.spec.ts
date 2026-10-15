import { test, expect } from "@playwright/test";

// Phase 5 E2E happy-path 冒烟(计划 §3.3):起 dev server + 后端 → Serenity 主题池扫描 → 面板展示层汇总
// 注1:stub health 直通启动门(BackendReadyProvider 轮询 /api/v1/health),同 feed-filter-verify 模式;
//      扫描端点(/api/v1/serenity/pool/scan)仍走真实后端 8088。
// 注2:pool scan 顺序扫描 ~30s,候选渲染等待放宽到 90s。
const BASE = "http://localhost:3000";

test.describe("Phase 5 Serenity happy-path 冒烟", () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`${BASE}/api/v1/health`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }),
    );
  });

  test("主题池扫描 → 面板展示层汇总", async ({ page }) => {
    await page.goto(`${BASE}/research/serenity`, { waitUntil: "domcontentloaded" });

    // 真实面板渲染(非启动门 / 骨架):默认单标的模式,切换按钮可见
    await expect(page.getByRole("button", { name: "单标的" })).toBeVisible({ timeout: 15000 });

    // 切主题池视图 → "扫描主题池"按钮出现
    await page.getByRole("button", { name: "主题池" }).click();
    await expect(page.getByRole("button", { name: "扫描主题池" })).toBeVisible({ timeout: 10000 });

    // 触发扫描(真实后端,~30s)
    await page.getByRole("button", { name: "扫描主题池" }).click();

    // 汇总 badge:候选 N / Top N / 弱证据 N
    const badge = page.locator("div").filter({ hasText: /候选 \d+/ }).first();
    await expect(badge).toBeVisible({ timeout: 90000 });

    const body = await page.locator("body").textContent();
    expect(body).toMatch(/候选 \d+|Top \d+|弱证据 \d+/);
  });
});
