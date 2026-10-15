import { test, expect } from "@playwright/test";

test("screenshot current /watch/kf", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: "screenshots/cur-watch-kf.png", fullPage: false });
});

test("screenshot design draft", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("file:///<local-path-redacted>", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: "screenshots/design-draft.png", fullPage: false });
});
