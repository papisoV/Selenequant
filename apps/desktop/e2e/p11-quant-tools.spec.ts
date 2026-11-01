import { test, expect } from "@playwright/test";

const BASE = "http://localhost:3000";

async function mockHealth(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) }),
  );
}

test("P5 量化工具:5 tab 渲染 + 套利 PRO 锁/演示解锁 + DCA + 画线", async ({ page }) => {
  await mockHealth(page);
  await page.goto(`${BASE}/watch/quant_tools`);
  await expect(page.getByRole("heading", { name: "量化工具" })).toBeVisible();

  // 5 个 tab
  const tabs = ["套利", "DCA", "跟单", "AI 网格", "画线"];
  for (const label of tabs) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }

  // 默认 tab = 套利:free 层级 → PRO 锁
  await expect(page.getByText("PRO 专享")).toBeVisible();
  await page.getByRole("button", { name: "查看演示" }).click();
  // demo 兜底(8s)后资金费率表出现 + 演示徽标
  await expect(page.locator(".feed-demo-badge").first()).toBeVisible({ timeout: 15000 });

  // DCA tab:表单 → 计划表
  await page.getByRole("button", { name: "DCA" }).click();
  await page.getByLabel("每期金额").fill("100");
  await page.getByLabel("期数").fill("3");
  await page.getByRole("button", { name: "生成计划表" }).click();
  await expect(page.locator(".dca-row")).toHaveCount(3);

  // 画线 tab:点击 K 线 → 买入价预填
  await page.getByRole("button", { name: "画线" }).click();
  await page.locator(".draw-bar").first().click();
  await expect(page.getByLabel("买入价")).not.toHaveValue("");
});
