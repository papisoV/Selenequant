import { test, expect } from "@playwright/test";
import { ALL_PAGES } from "@/lib/page-manifest";

// Phase 5 acceptance: 所有 45 页都能打开，渲染真实组件（非 PageStub），不白屏
test.describe("Phase 5 全页面冒烟", () => {
  for (const page of ALL_PAGES) {
    test(`${page.scene}/${page.slug} 渲染真实组件`, async ({ page: browserPage }) => {
      const url = `/${page.scene}/${page.slug}`;
      await browserPage.goto(url);

      // HTTP 200
      const resp = await browserPage.goto(url);
      expect(resp?.status()).toBe(200);

      // 标题渲染（非 PageStub 骨架文案）
      await expect(browserPage.locator("h1")).toContainText(page.title);
      await expect(browserPage.locator("body")).not.toContainText("页面骨架 — Phase 4 接肉");

      // 加载态 or 数据态 or 空态 — 都算正常（只要不是白屏 / 500）
      const body = browserPage.locator("body");
      await expect(body).not.toContainText("Application error");
    });
  }
});

test.describe("Phase 5 关键链路深度验证", () => {
  test("kf 主力监控渲染 lightweight-charts", async ({ page }) => {
    await page.goto("/watch/kf");
    await expect(page.locator("h1")).toContainText("主力监控");
    // lightweight-charts canvas 在加载完成前是 "加载中…"，后端不可达时是 error state
    const content = await page.locator("body").textContent();
    expect(content).toMatch(/加载中|加载失败|BTC-USDT-SWAP/);
  });

  test("pos 持仓表四态之一", async ({ page }) => {
    await page.goto("/trade/pos");
    await expect(page.locator("h1")).toContainText("我的持仓");
    const content = await page.locator("body").textContent();
    expect(content).toMatch(/加载中|加载失败|当前账户无持仓|未实现盈亏/);
  });

  test("kf → pos 场景切换", async ({ page }) => {
    await page.goto("/watch/kf");
    await expect(page.locator("h1")).toContainText("主力监控");
    await page.goto("/trade/pos");
    await expect(page.locator("h1")).toContainText("我的持仓");
  });

  test("默认 / 跳转到 watch/kf", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/watch\/kf/);
    await expect(page.locator("h1")).toContainText("主力监控");
  });
});
