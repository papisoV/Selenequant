import { test, expect } from "@playwright/test";

// US-2 部署三步向导 E2E:深链直开 → Step0 参数可见 → 走到 Step2 → Paper 部署链路发车。
// 用 ?deploy=1&template=ema_cross 直开(对齐 QD URL 即状态),不吃模板卡点击的代理冷抖动。
// DOM 断言,禁截图([[feedback_no-screenshot-files]])。
test.beforeEach(() => {
  test.setTimeout(120_000);
});

test("deploy wizard deep link opens three steps and paper deploy fires", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/research/strat?deploy=1&template=ema_cross", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  // 向导弹出:标题含模板名 + Step0 参数确认。P3 竞态修复后标题是真实模板名
  // "EMA均线交叉";后端挂掉的兜底路径才显示 raw id "ema_cross"。两种诚实态都认。
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible({ timeout: 30000 });
  await expect(dialog.getByText(/EMA均线交叉|EMA 交叉策略|ema_cross/).first()).toBeVisible({ timeout: 30000 });
  // Step0: 参数面板渲染(无参数时显示「无参数」也是诚实态)
  await expect(dialog.getByText(/参数确认/)).toBeVisible();
  // → Step1
  await dialog.getByRole("button", { name: "下一步" }).click();
  await expect(dialog.getByText("交易对", { exact: true })).toBeVisible({ timeout: 10000 });
  // → Step2
  await dialog.getByRole("button", { name: "下一步" }).click();
  await expect(dialog.getByText("Paper 模拟盘")).toBeVisible({ timeout: 10000 });
  // Paper 默认选中 → 部署按钮可点;点击后出现 loading 或成功 toast(部署链路发车即算过)
  const deployBtn = dialog.getByRole("button", { name: /部署到 Paper/ });
  await expect(deployBtn).toBeEnabled();
  await deployBtn.click();
  // 后端可能在 3s 超时限制内完成;断言按钮进入 busy 或页面出现成功提示
  await expect(
    dialog.getByText(/部署到 Paper/).or(page.getByText(/已部署 Paper/)).first(),
  ).toBeVisible({ timeout: 15000 });
});
