import { test, expect, type Page } from "@playwright/test";
import { mockHealth } from "./mock-health";

// 多副本环境:用 E2E_PORT 指本副本前端端口(playwright.config.ts 同口径)
const BASE = `http://localhost:${process.env.E2E_PORT ?? "3000"}`;

const RUNNABLE = {
  id: "qd:src48",
  name: "XLM 波动率突破",
  file: "src48_XLMUSDT_gk_vol_trail10_AlphaMaster.py", // cleanroom: external QD export filename
  symbol: "XLM/USDT",
  instrument: "XLM-USDT-SWAP",
  exchange: "okx",
  market_type: "futures",
  interval: "1h",
  warmup: 320,
  direction_mode: "both",
  params: { trail: 0.1 },
  runnable: true,
  qd_baseline: {
    metrics: { total_return: 0.946, max_drawdown: -0.252, sharpe: 1.61 },
    candidates: 1,
    reason: "anchored after final '->'",
  },
  flags: ["in_sample_only"],
};

const GATED = {
  id: "qd:src41",
  name: "组合动量",
  file: "src41_portfolio_momentum.py",
  symbol: "",
  exchange: null,
  market_type: "futures",
  interval: "1h",
  warmup: 0,
  params: {},
  runnable: false,
  reason: "defines no callable handle_data",
  qd_baseline: { metrics: null, candidates: 0, reason: "no anchored figure" },
  flags: ["portfolio", "multi_instrument"],
};

/** Serve the QD library endpoint; the page's other fetches are left to fail
 * on their own (they render empty sections, which is not under test here). */
async function mockQdLibrary(page: Page, data: unknown[]): Promise<void> {
  await page.route("**/api/v1/strategies/qd-templates", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data, meta: { total: data.length }, error: null }),
    }),
  );
}

test("US-107 QD 策略库:卡片渲染 + runnable 门控", async ({ page }) => {
  await mockHealth(page);
  await mockQdLibrary(page, [RUNNABLE, GATED]);
  await page.goto(`${BASE}/research/strat`);

  const section = page.locator(".rp-card").filter({ hasText: "QuantDinger 策略库" });
  await expect(section.locator("span", { hasText: "QuantDinger 策略库 (2)" })).toBeVisible();

  // 可运行卡:标的/周期来自源声明(只读),QD 基准三行
  const runnableCard = section.getByTestId("strat-card").filter({ hasText: "qd:src48" });
  await expect(runnableCard.getByText("XLM 波动率突破")).toBeVisible();
  await expect(runnableCard.getByText("XLM/USDT")).toBeVisible();
  await expect(runnableCard.getByText("1h", { exact: true })).toBeVisible();
  await expect(runnableCard.getByText("+94.60%")).toBeVisible();
  await expect(runnableCard.getByText("-25.20%")).toBeVisible();
  await expect(runnableCard.getByText("1.61")).toBeVisible();
  await expect(runnableCard.getByText("样本内")).toBeVisible();
  await expect(runnableCard.getByRole("button", { name: "回测" })).toBeEnabled();
  await expect(runnableCard.getByText(/不可运行/)).toHaveCount(0);

  // 门控卡:按钮禁用 + 理由可见 + QD 未报数字时标「未提供」而非编数
  const gatedCard = section.getByTestId("strat-card").filter({ hasText: "qd:src41" });
  await expect(gatedCard.getByRole("button", { name: "回测" })).toBeDisabled();
  await expect(gatedCard.getByText(/不可运行:defines no callable handle_data/)).toBeVisible();
  await expect(gatedCard.getByText(/QD 未提供结构化绩效/)).toBeVisible();
  // exact: 卡名「组合动量」也含「组合」,不精确匹配会撞上两个元素
  await expect(gatedCard.getByText("组合", { exact: true })).toBeVisible();
  await expect(gatedCard.getByText("多标的", { exact: true })).toBeVisible();
});

test("US-107 QD 策略库:空库显示空态而非错误", async ({ page }) => {
  await mockHealth(page);
  await mockQdLibrary(page, []);
  await page.goto(`${BASE}/research/strat`);

  const section = page.locator(".rp-card").filter({ hasText: "QuantDinger 策略库" });
  await expect(section.locator("span", { hasText: "QuantDinger 策略库 (0)" })).toBeVisible();
  await expect(section.getByText(/SELENE_QD_STRATEGY_DIR/)).toBeVisible();
});

test("US-107 QD 卡:回测请求只带 strategy_id,不带 symbol/interval", async ({ page }) => {
  await mockHealth(page);
  await mockQdLibrary(page, [RUNNABLE]);

  const posted: unknown[] = [];
  await page.route("**/api/v1/backtests", async (route) => {
    posted.push(route.request().postDataJSON());
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ data: { task_id: "t1" }, meta: {}, error: null }),
    });
  });
  // 轮询立刻落定,否则卡片会等满 5 分钟超时
  await page.route("**/api/v1/backtests/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: { status: "completed" }, meta: {}, error: null }),
    }),
  );

  await page.goto(`${BASE}/research/strat`);
  const card = page
    .locator(".rp-card")
    .filter({ hasText: "QuantDinger 策略库" })
    .getByTestId("strat-card")
    .filter({ hasText: "qd:src48" });
  await card.getByRole("button", { name: "回测" }).click();

  await expect.poll(() => posted.length).toBe(1);
  // 源的声明驱动运行:标的/周期由源决定,请求体不得覆盖
  expect(posted[0]).toEqual({ strategy_id: "qd:src48" });
});
