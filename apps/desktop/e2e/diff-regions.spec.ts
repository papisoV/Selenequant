import { test } from "@playwright/test";

test("diff current vs design - count elements per region", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  const stats = await page.evaluate(() => {
    function stats(sel: string) {
      const el = document.querySelector(sel);
      if (!el) return { found: false, count: 0, text: "" };
      return {
        found: true,
        count: el.querySelectorAll("*").length,
        text: el.textContent?.slice(0, 200).replace(/\s+/g, " ") ?? "",
      };
    }
    return {
      vhero: stats(".vhero"),
      sidebar: stats(".sidebar"),
      symbolBar: stats(".symbol-bar"),
      ctxBar: stats(".ctx-bar"),
      chartMain: stats(".chart-main"),
      rightPanel: stats(".right-panel"),
      drawer: stats(".drawer"),
      feedBar: stats(".feed-bar"),
    };
  });
  console.log("=== CURRENT ===\n" + JSON.stringify(stats, null, 2));
});

test("diff design draft - count elements per region", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("file:///<local-path-redacted>", { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  const stats = await page.evaluate(() => {
    function stats(sel: string) {
      const el = document.querySelector(sel);
      if (!el) return { found: false, count: 0, text: "" };
      return {
        found: true,
        count: el.querySelectorAll("*").length,
        text: el.textContent?.slice(0, 200).replace(/\s+/g, " ") ?? "",
      };
    }
    return {
      vhero: stats(".vhero"),
      sidebar: stats(".sidebar"),
      symbolBar: stats(".symbol-bar"),
      ctxBar: stats(".ctx-bar"),
      chartMain: stats(".chart-main"),
      rightPanel: stats(".right-panel"),
      drawer: stats(".drawer"),
      feedBar: stats(".feed-bar"),
    };
  });
  console.log("=== DESIGN ===\n" + JSON.stringify(stats, null, 2));
});
