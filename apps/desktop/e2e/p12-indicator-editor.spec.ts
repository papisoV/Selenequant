import { test, expect } from "@playwright/test";

const BASE = "http://localhost:3000";
const YAML_TEMPLATE_COUNT = 26;

async function mockHealth(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) }),
  );
}

test("P6 指标编辑器:画廊≥26 条 + 选参公式串 + 回测 demo", async ({ page }) => {
  await mockHealth(page);
  await page.goto(`${BASE}/research/indicator_editor`);
  await expect(page.getByRole("heading", { name: "自定义指标编辑器" })).toBeVisible();

  // 模板画廊:条目数 ≥ 实读 templates/*.yaml 数量
  const cardCount = await page.locator(".indicator-card").count();
  expect(cardCount).toBeGreaterThanOrEqual(YAML_TEMPLATE_COUNT);

  // 选指标 → 公式串预览
  await page.getByTestId("indicator-card-template-ema-cross").click();
  await expect(page.getByText("EMA Crossover( 快线 EMA = 12, 慢线 EMA = 26 )")).toBeVisible();

  // 改参数 → 公式串实时更新
  await page.getByLabel("快线 EMA").fill("5");
  await expect(page.getByText("EMA Crossover( 快线 EMA = 5, 慢线 EMA = 26 )")).toBeVisible();

  // 运行回测 → 后端不可达 → 8s demo 结果 + 演示徽标
  await page.getByRole("button", { name: "运行回测" }).click();
  await expect(page.locator(".backtest-result-panel")).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".feed-demo-badge").first()).toBeVisible();
  await expect(page.getByText("总收益")).toBeVisible();
});
