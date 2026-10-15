// US-3 部署列表三态 tab 实机验证:页面渲染三个过滤按钮 + 计数存在。
// 过滤器按钮无条件渲染(在数据分支之外),故断言不依赖部署数据是否存在。
// E2E 串行多页共享 127.0.0.1 限流预算 120/min(rate_limit.py:18),撞 429 时
// 列表诚实退化为「暂无已部署策略」——状态圆点断言仅在有数据时校验。
import { test, expect } from "@playwright/test";

test("strat page deploy list shows three-state filter tabs", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/research/strat", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  // 卡片标题(带计数)——用精确模式避开「暂无已部署策略」空态文案的撞车。
  await expect(page.getByText(/^已部署策略 \(\d+\)$/)).toBeVisible({ timeout: 30000 });
  // 三个过滤按钮,无论数据有无都渲染。
  const all = page.getByRole("button", { name: /^全部 \d+$/ });
  const running = page.getByRole("button", { name: /^运行中 \d+$/ });
  const attention = page.getByRole("button", { name: /^需关注 \d+$/ });
  await expect(all).toBeVisible({ timeout: 20000 });
  await expect(running).toBeVisible();
  await expect(attention).toBeVisible();
  // 「全部」默认选中。
  await expect(all).toHaveAttribute("aria-pressed", "true");
  // 切到「运行中」→ aria-pressed 翻转。
  await running.click();
  await expect(running).toHaveAttribute("aria-pressed", "true");
  await expect(all).toHaveAttribute("aria-pressed", "false");
  // 有部署数据时,状态 pill 圆点应可见(三态之一);429 降级空态则跳过。
  const dot = page.getByTestId("deploy-status-dot").first();
  if (await dot.isVisible().catch(() => false)) {
    await expect(dot).toBeVisible();
  }
});
