import { test, expect } from "@playwright/test";

const REGIONS = [
  { name: "vhero", selector: ".vhero" },
  { name: "sidebar", selector: ".sidebar" },
  { name: "symbolBar", selector: ".symbol-bar" },
  { name: "ctxBar", selector: ".ctx-bar" },
  { name: "chartMain", selector: ".chart-main" },
  { name: "rightPanel", selector: ".right-panel" },
  { name: "drawer", selector: ".drawer" },
  { name: "feedBar", selector: ".feed-bar" },
];

test.describe.serial("region-by-region visual diff", () => {
  for (const r of REGIONS) {
    test(`compare ${r.name}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });

      // Current
      await page.goto("http://localhost:3000/watch/kf", { waitUntil: "networkidle" });
      await page.waitForTimeout(2500);
      const curEl = page.locator(r.selector).first();
      const curCount = await curEl.count();
      let curBox: { x: number; y: number; width: number; height: number } | null = null;
      if (curCount > 0) {
        curBox = await curEl.boundingBox();
        if (curBox) {
          await page.screenshot({
            path: `screenshots/cur-${r.name}.png`,
            clip: curBox,
          });
        }
      }

      // Design
      await page.goto(
        "file:///<local-path-redacted>",
        { waitUntil: "networkidle" }
      );
      await page.waitForTimeout(2500);
      const desEl = page.locator(r.selector).first();
      const desCount = await desEl.count();
      let desBox: { x: number; y: number; width: number; height: number } | null = null;
      if (desCount > 0) {
        desBox = await desEl.boundingBox();
        if (desBox) {
          await page.screenshot({
            path: `screenshots/des-${r.name}.png`,
            clip: desBox,
          });
        }
      }

      console.log(
        `=== ${r.name} ===\n` +
        `  cur: found=${curCount > 0} box=${curBox ? `${curBox.width}x${curBox.height} @(${curBox.x},${curBox.y})` : "null"}\n` +
        `  des: found=${desCount > 0} box=${desBox ? `${desBox.width}x${desBox.height} @(${desBox.x},${desBox.y})` : "null"}`
      );
    });
  }

  test("full page side-by-side", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: "screenshots/cur-full.png", fullPage: false });

    await page.goto(
      "file:///<local-path-redacted>",
      { waitUntil: "domcontentloaded" }
    );
    await page.waitForTimeout(2500);
    await page.screenshot({ path: "screenshots/des-full.png", fullPage: false });
  });

  test("computed style comparison", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);

    const curStyles = await page.evaluate(() => {
      const get = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        if (!el) return null;
        const cs = getComputedStyle(el);
        return {
          fontFamily: cs.fontFamily.slice(0, 80),
          fontSize: cs.fontSize,
          background: cs.backgroundColor,
          color: cs.color,
          width: el.clientWidth,
          height: el.clientHeight,
        };
      };
      return {
        body: get("body"),
        vhero: get(".vhero"),
        sidebar: get(".sidebar"),
        chartMain: get(".chart-main"),
        rightPanel: get(".right-panel"),
        drawer: get(".drawer"),
      };
    });

    await page.goto(
      "file:///<local-path-redacted>",
      { waitUntil: "domcontentloaded" }
    );
    await page.waitForTimeout(2500);

    const desStyles = await page.evaluate(() => {
      const get = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        if (!el) return null;
        const cs = getComputedStyle(el);
        return {
          fontFamily: cs.fontFamily.slice(0, 80),
          fontSize: cs.fontSize,
          background: cs.backgroundColor,
          color: cs.color,
          width: el.clientWidth,
          height: el.clientHeight,
        };
      };
      return {
        body: get("body"),
        vhero: get(".vhero"),
        sidebar: get(".sidebar"),
        chartMain: get(".chart-main"),
        rightPanel: get(".right-panel"),
        drawer: get(".drawer"),
      };
    });

    console.log("=== CURRENT STYLES ===\n" + JSON.stringify(curStyles, null, 2));
    console.log("=== DESIGN STYLES ===\n" + JSON.stringify(desStyles, null, 2));
  });
});
