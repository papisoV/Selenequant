import { test } from "@playwright/test";

test("responsive layout check", async ({ page }) => {
  const sizes = [
    { w: 1920, h: 1080, label: "1920x1080" },
    { w: 1440, h: 900, label: "1440x900" },
    { w: 1280, h: 800, label: "1280x800" },
    { w: 1024, h: 768, label: "1024x768" },
  ];

  for (const size of sizes) {
    await page.setViewportSize({ width: size.w, height: size.h });
    await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    
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
          scrollW: el.scrollWidth, clientW: el.clientWidth,
          scrollH: el.scrollHeight, clientH: el.clientHeight,
          overflowX: el.scrollWidth > el.clientWidth,
          overflowY: el.scrollHeight > el.clientHeight,
        };
      };
      return {
        viewport: { w: window.innerWidth, h: window.innerHeight },
        appShell: getBox(".app-shell"),
        vhero: getBox(".vhero"),
        sidebar: getBox(".sidebar"),
        main: getBox(".main"),
        symbolBar: getBox(".symbol-bar"),
        ctxBar: getBox(".ctx-bar"),
        sceneLayout: getBox(".scene-layout"),
        chartArea: getBox(".chart-area"),
        chartMain: getBox(".chart-main"),
        rightPanel: getBox(".right-panel"),
        drawer: getBox(".drawer"),
        feedBar: getBox(".feed-bar"),
        chartMainOverflow: overflowCheck(".chart-main"),
        rightPanelOverflow: overflowCheck(".right-panel"),
        sceneLayoutOverflow: overflowCheck(".scene-layout"),
      };
    });
    
    console.log(`\n=== ${size.label} ===`);
    console.log(JSON.stringify(info, null, 2));
  }
});
