import { test, expect } from "@playwright/test";

// US-0b 三栏工作台验证:头部真值条+灵魂问句 / 左栏参数常驻 / 主区三tab / 右栏 AI。
// 用 ?view=workbench 直入,不等 State(State 经 dev 代理偶发 20s+,存量坑)。
// DOM 断言,禁截图([[feedback_no-screenshot-files]])。
test.beforeEach(() => {
  test.setTimeout(120_000);
});

test("workbench three-column layout mounts with real strategy data", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/research/strategy-cockpit?id=strat-6395c312&view=workbench", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  // 头部:灵魂问句 + 动作按钮
  await expect(page.getByText("这次改动让策略更好了吗?")).toBeVisible({ timeout: 60000 });
  await expect(page.getByRole("button", { name: /跑回测/ })).toBeVisible({ timeout: 60000 });
  await expect(page.getByRole("button", { name: /保存|已同步/ })).toBeVisible();
  // 主区三 tab
  await expect(page.getByRole("tab", { name: "回测" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "部署监控" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "版本历史" })).toBeVisible();
  // 左栏参数常驻:ParamPanel 渲染(「参数」是 param-panel 左栏标题)
  await expect(page.getByText("风控参数")).toBeVisible({ timeout: 60000 });
  // 右栏 AiSidebar:chat 输入框可见
  await expect(page.getByPlaceholder("描述你想做的策略...")).toBeVisible({ timeout: 60000 });
});

test("workbench backtest tab shows honest empty state", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/research/strategy-cockpit?id=strat-6395c312&view=workbench", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  // 军规5:无回测结果 → 教学空态,非空白
  await expect(page.getByText("还没有本版回测结果")).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("调好左栏参数和上方区间")).toBeVisible();
});

test("workbench monitor tab shows honest empty state with deploy entry", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/research/strategy-cockpit?id=strat-6395c312&view=workbench", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.getByRole("tab", { name: "部署监控" }).click();
  // 军规5:监控 tab 必落在三种诚实态之一,不许空白/骨架卡死。
  // (E2E 串行多页共享 127.0.0.1 限流预算 120/min,REST 初值可能撞 429 → 失败态也算诚实)
  const emptyState = page.getByText("该策略没有运行中的部署实例");
  const runningPanel = page.getByRole("button", { name: /停止部署/ });
  const errorState = page.getByText(/部署状态加载失败/);
  await expect(emptyState.or(runningPanel).or(errorState)).toBeVisible({ timeout: 30000 });
});

test("workbench versions tab loads version timeline", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/research/strategy-cockpit?id=strat-6395c312&view=workbench", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.getByRole("tab", { name: "版本历史" }).click();
  // 有版本 → 点开折叠头见「当前」徽章;空/拉取失败 → 教学空态。两种都是诚实渲染,不许空白。
  const emptyState = page.getByText("还没有版本记录");
  const collapseHeader = page.getByRole("button", { name: /^版本历史 \d+$/ });
  await expect(emptyState.or(collapseHeader)).toBeVisible({ timeout: 60000 });
  if (await collapseHeader.isVisible()) {
    await collapseHeader.click();
    await expect(page.getByText("当前", { exact: true })).toBeVisible({ timeout: 60000 });
  }
});
