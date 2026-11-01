import { test } from "@playwright/test";

test("structure diff", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  
  // Current
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  const cur = await page.evaluate(() => {
    const dump = (sel: string, max = 3) => {
      const els = document.querySelectorAll(sel);
      const out: string[] = [];
      for (let i = 0; i < Math.min(els.length, max); i++) {
        const el = els[i] as HTMLElement;
        const cls = el.className?.toString().slice(0, 80) || "";
        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || "").trim().slice(0, 40);
        out.push(`<${tag} class="${cls}">${text}</${tag}>`);
      }
      return out;
    };
    return {
      vhero: dump(".vhero > *", 10),
      vheroNav: dump(".vhero-nav > *", 10),
      sidebar: dump(".sidebar > *", 10),
      chartMain: dump(".chart-main > *", 8),
      chartToolbar: dump(".chart-toolbar, .ctx-bar > *", 8),
      rightPanel: dump(".right-panel > *", 10),
      drawer: dump(".drawer > *", 10),
      feedBar: dump(".feed-bar > *", 8),
    };
  });

  // Design
  await page.goto(
    "file:///<local-path-redacted>",
    { waitUntil: "domcontentloaded" }
  );
  await page.waitForTimeout(4000);
  const des = await page.evaluate(() => {
    const dump = (sel: string, max = 3) => {
      const els = document.querySelectorAll(sel);
      const out: string[] = [];
      for (let i = 0; i < Math.min(els.length, max); i++) {
        const el = els[i] as HTMLElement;
        const cls = el.className?.toString().slice(0, 80) || "";
        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || "").trim().slice(0, 40);
        out.push(`<${tag} class="${cls}">${text}</${tag}>`);
      }
      return out;
    };
    return {
      vhero: dump(".vhero > *", 10),
      vheroNav: dump(".vhero-nav > *", 10),
      sidebar: dump(".sidebar > *", 10),
      chartMain: dump(".chart-main > *", 8),
      chartToolbar: dump(".chart-toolbar, .ctx-bar > *", 8),
      rightPanel: dump(".right-panel > *", 10),
      drawer: dump(".drawer > *", 10),
      feedBar: dump(".feed-bar > *", 8),
    };
  });

  console.log("\n=== STRUCTURE DIFF ===");
  const keys = Array.from(new Set([...Object.keys(cur), ...Object.keys(des)]));
  for (const k of keys) {
    console.log(`\n[${k}] CUR (${cur[k]?.length || 0}):`);
    for (const s of cur[k] || []) console.log(`  ${s}`);
    console.log(`[${k}] DES (${des[k]?.length || 0}):`);
    for (const s of des[k] || []) console.log(`  ${s}`);
  }
});
