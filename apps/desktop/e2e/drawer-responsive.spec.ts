import { test } from "@playwright/test";

test("drawer height responsive", async ({ page }) => {
  const sizes = [
    { w: 1920, h: 1080 },
    { w: 1024, h: 768 },
    { w: 800, h: 600 },
  ];
  for (const size of sizes) {
    await page.setViewportSize({ width: size.w, height: size.h });
    await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    const drawer = await page.evaluate(() => {
      const el = document.querySelector(".drawer") as HTMLElement;
      return { h: el?.clientHeight, computed: getComputedStyle(el).height };
    });
    console.log(`${size.w}x${size.h}: drawer h=${drawer.h} computed=${drawer.computed}`);
  }
});
