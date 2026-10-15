import { test, expect } from "@playwright/test";

test("MEMORY 页渲染分层与折叠条", async ({ page }) => {
  await page.goto("http://localhost:3002/research/mem", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("组织学到的,不是组织存过的")).toBeVisible({ timeout: 30000 });
  await expect(page.getByText(/既有知识/)).toBeVisible();
  await expect(page.getByText(/待检验线索 · 13 条/)).toBeVisible();
  // 展开折叠条 → 线索行可见
  await page.getByText(/待检验线索 · 13 条/).click();
  await expect(page.getByText("smoke 判断第一版")).toBeVisible();
});
