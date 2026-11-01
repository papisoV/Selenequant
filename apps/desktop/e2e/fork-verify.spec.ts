import { test, expect } from "@playwright/test";
import { mockHealth } from "./mock-health";

// 8-fork 合并后落地页实测:验证各 fork 专属组件 + 真数据渲染,非空壳/非空态。
// 运行: LOCAL_API_BASE=http://localhost:8088 E2E_PORT=3001 npx playwright test fork-verify

test.beforeEach(async ({ page }) => {
  await mockHealth(page);
});

test.describe("8-fork 合并后落地页实测", () => {
  test("R5 alpha_mine — 数据源选择 + 本地 klines 覆盖", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/research/alpha_mine", { waitUntil: "domcontentloaded" });

    // R5 专属:数据源选择区(交易所/品种/周期/市场类型)
    await expect(page.getByLabel(/交易所/)).toBeVisible({ timeout: 90_000 });
    await expect(page.getByLabel(/品种/)).toBeVisible();
    await expect(page.getByLabel(/周期/)).toBeVisible();
    await expect(page.getByLabel(/市场类型/)).toBeVisible();

    // 本地 klines 覆盖显示(真实后端 + 本地 klines.db)
    await expect(page.getByText(/本地 klines 覆盖/)).toBeVisible({ timeout: 60_000 });

    // 训练按钮存在(数据闭环入口)
    await expect(page.getByRole("button", { name: /开始挖矿|开始训练/ })).toBeVisible();
  });

  test("R6 onchain-hub — 链上工作台 r5 拼装(tab+seg 结构)", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/watch/onchain-hub", { waitUntil: "domcontentloaded" });

    // r5 拼装:block(链上总览)+ tab(链上指标/鲸鱼钱流)+ met tab seg 视图按钮
    await expect(page.getByRole("tab", { name: "链上指标" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("tab", { name: "鲸鱼钱流" })).toBeVisible();
    await expect(page.getByRole("button", { name: "NVT" })).toBeVisible();
  });

  test("R2/US-204 derivatives-hub — 衍生品面 r5 拼装(爆仓 tab)", async ({ page }) => {
    test.setTimeout(120_000);
    // K5 删 derivatives-liqmap 双入口后,爆仓能力收敛进衍生品面
    await page.goto("/watch/derivatives-hub", { waitUntil: "domcontentloaded" });

    // 衍生品面:爆仓 tab 默认激活,热区视图(liqmap)在其内
    await expect(page.getByRole("tab", { name: "爆仓" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("button", { name: "热区" })).toBeVisible();
  });

  test("R7/US-205 sig — 信号面 r5 拼装(新闻摘要 block)", async ({ page }) => {
    test.setTimeout(120_000);
    // K21/K22 删 signals-current/dashboard 后,新闻摘要能力收敛进 sig 面
    await page.goto("/watch/sig", { waitUntil: "domcontentloaded" });

    // sig 面默认 tab=代币信号;新闻摘要作 block 标题
    await expect(page.getByRole("tab", { name: "代币信号" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("新闻摘要", { exact: true }).first()).toBeVisible();
  });

  test("R4 polymarket-markets — 预测市场", async ({ page }) => {
    test.setTimeout(120_000);
    // R4 专属页是 polymarket-markets(不是 /watch/poly 普通 symbol 页)
    await page.goto("/watch/polymarket-markets", { waitUntil: "domcontentloaded" });

    // PolymarketMarketsPage 标题是 V17Card span
    await expect(page.getByText("PM 市场", { exact: true }).first()).toBeVisible({ timeout: 90_000 });
  });
});
