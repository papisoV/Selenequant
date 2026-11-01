import { test } from "@playwright/test";

test("capture full screenshots", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: "screenshots/cur-full.png", fullPage: false });
  
  await page.goto(
    "file:///<local-path-redacted>",
    { waitUntil: "domcontentloaded" }
  );
  await page.waitForTimeout(4000);
  await page.screenshot({ path: "screenshots/des-full.png", fullPage: false });
});
