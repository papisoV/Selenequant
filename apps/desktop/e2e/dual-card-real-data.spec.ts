import { test, expect } from "@playwright/test";

// WS-B 双卡真实渲染 e2e:真实 local-api(LOCAL_API_BASE 指向本副本后端)+ klines.db,
// 非 mock。断言 IC 诊断面板 + 回报面板渲染真实跨截面数据(非空态)。
// 运行: LOCAL_API_BASE=http://localhost:8090 E2E_PORT=3001 npx playwright test dual-card-real-data
test.describe("WS-B 双卡真实渲染", () => {
  test("IC 诊断 + 回报面板渲染真实跨截面数据", async ({ page }) => {
    test.setTimeout(150_000);
    await page.goto("/research/rsch", { waitUntil: "domcontentloaded" });

    // IC 诊断卡:标题 → 统计格 → 图表(svg role=img 出现即非空)
    await expect(page.getByText("IC 诊断面板 · 跨截面动量")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Mean IC", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("IR", { exact: true })).toBeVisible();
    await expect(page.getByText("t-stat", { exact: true })).toBeVisible();
    await expect(page.getByRole("img", { name: "IC / Rolling IR" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("累计 IC", { exact: true })).toBeVisible();
    await expect(page.getByText("IC 自相关", { exact: true })).toBeVisible();
    await expect(page.getByText("月度 IC 热图", { exact: true })).toBeVisible();
    await expect(page.getByText(/QQ 正态诊断：r =/)).toBeVisible();

    // 回报面板:标题 → 统计格 → 累计 LS 双线图 + 换手
    await expect(page.getByText("多空回报面板 · 权重漂移回测")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Ann. Return", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Ann. Sharpe", { exact: true })).toBeVisible();
    await expect(page.getByText("Max Drawdown", { exact: true })).toBeVisible();
    await expect(page.getByRole("img", { name: "Return Before Cost / Cumulative LS Return" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("换手率", { exact: true })).toBeVisible();

    // 双卡均为真实数据:无"暂无足够"空态文案
    await expect(page.getByText(/暂无足够跨截面数据/)).toHaveCount(0);
    await expect(page.getByText(/暂无足够回测数据/)).toHaveCount(0);
  });
});
