import { test } from "@playwright/test";

test("responsive screenshots", async ({ page }) => {
  const sizes = [
    { w: 1920, h: 1080, label: "1920" },
    { w: 1440, h: 900, label: "1440" },
    { w: 1280, h: 800, label: "1280" },
    { w: 1024, h: 768, label: "1024" },
  ];
  for (const size of sizes) {
    await page.setViewportSize({ width: size.w, height: size.h });
    await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `screenshots/cur-${size.label}.png`, fullPage: false });
  }
});
