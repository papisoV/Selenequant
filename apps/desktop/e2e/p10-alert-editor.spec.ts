import { test, expect } from "@playwright/test";
import { mockHealth } from "./mock-health";

// 必须用 localhost 而非 127.0.0.1:Next 16 dev 默认 allowedDevOrigins 只放行 localhost
const BASE = "http://localhost:3000";

test.describe("P10 预警规则编辑器", () => {
  test("1. 新建→列表;开关生效;刷新持久;删除(confirm)", async ({ page }) => {
    // 接受 window.confirm(删除确认)
    page.on("dialog", (d) => d.accept());
    await mockHealth(page);

    const resp = await page.goto(`${BASE}/watch/intelligence-alerts-rules`);
    expect(resp?.status()).toBe(200);
    await page.waitForLoadState("networkidle");

    // 空态可见
    await expect(page.getByText("暂无规则,点击「+ 新建规则」创建")).toBeVisible();

    // 新建
    await page.getByRole("button", { name: "+ 新建规则" }).click();
    await page.getByLabel("规则名称").fill("E2E 价格预警");
    await page.getByLabel("价格阈值").fill("50000");
    await page.getByRole("button", { name: "保存规则" }).click();

    // 卡片出现
    const card = page.locator(".alert-rule-card", { hasText: "E2E 价格预警" });
    await expect(card).toBeVisible();

    // 开关生效:aria-checked 翻转
    const sw = card.locator('[role="switch"]');
    const before = await sw.getAttribute("aria-checked");
    await sw.click();
    await expect(sw).toHaveAttribute("aria-checked", before === "true" ? "false" : "true");

    // 刷新后仍在(localStorage 持久)
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".alert-rule-card", { hasText: "E2E 价格预警" })).toBeVisible();

    // 删除(confirm → accept → 生效)
    await page
      .locator(".alert-rule-card", { hasText: "E2E 价格预警" })
      .getByRole("button", { name: "删除" })
      .click();
    await expect(page.locator(".alert-rule-card", { hasText: "E2E 价格预警" })).toHaveCount(0);

    await page.screenshot({ path: "e2e/screenshots/p10-alert-editor.png", fullPage: true });
  });
});
