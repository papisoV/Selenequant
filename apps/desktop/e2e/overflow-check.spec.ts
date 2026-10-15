import { test } from "@playwright/test";

test("overflow check", async ({ page }) => {
  const sizes = [
    { w: 1920, h: 1080 },
    { w: 1440, h: 900 },
    { w: 1024, h: 768 },
  ];
  for (const size of sizes) {
    await page.setViewportSize({ width: size.w, height: size.h });
    await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    
    const info = await page.evaluate(() => {
      const sels = [".app-shell", ".main", ".scene-layout", ".chart-area", ".chart-main", ".right-panel", ".drawer", ".feed-bar", ".symbol-bar", ".ctx-bar"];
      const out: Record<string, any> = {};
      for (const s of sels) {
        const el = document.querySelector(s) as HTMLElement;
        if (!el) { out[s] = null; continue; }
        out[s] = {
          w: el.clientWidth, h: el.clientHeight,
          scrollW: el.scrollWidth, scrollH: el.scrollHeight,
          overflowX: el.scrollWidth > el.clientWidth,
          overflowY: el.scrollHeight > el.clientHeight,
        };
      }
      // Check body horizontal overflow
      out.body = { scrollW: document.body.scrollWidth, clientW: document.body.clientWidth, overflowX: document.body.scrollWidth > document.body.clientWidth };
      return out;
    });
    
    console.log(`\n=== ${size.w}x${size.h} ===`);
    for (const [k, v] of Object.entries(info)) {
      if (v) console.log(`  ${k}: w=${v.w} h=${v.h} scrollW=${v.scrollW} scrollH=${v.scrollH} ovX=${v.overflowX} ovY=${v.overflowY}`);
    }
  }
});
