import { test } from "@playwright/test";

test("design draft responsive", async ({ page }) => {
  const sizes = [
    { w: 1920, h: 1080, label: "1920x1080" },
    { w: 1440, h: 900, label: "1440x900" },
    { w: 1024, h: 768, label: "1024x768" },
  ];

  for (const size of sizes) {
    await page.setViewportSize({ width: size.w, height: size.h });
    await page.goto(
      "file:///<local-path-redacted>",
      { waitUntil: "domcontentloaded" }
    );
    await page.waitForTimeout(2000);
    
    const info = await page.evaluate(() => {
      const getBox = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) };
      };
      const overflowCheck = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        if (!el) return null;
        return {
          scrollH: el.scrollHeight, clientH: el.clientHeight,
          overflowY: el.scrollHeight > el.clientHeight,
        };
      };
      return {
        appShell: getBox(".app-shell"),
        rightPanel: getBox(".right-panel"),
        chartMain: getBox(".chart-main"),
        drawer: getBox(".drawer"),
        rightPanelOverflow: overflowCheck(".right-panel"),
      };
    });
    
    console.log(`\n=== DESIGN ${size.label} ===`);
    console.log(JSON.stringify(info, null, 2));
  }
});
