import { test } from "@playwright/test";

test("vhero sidebar diff", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  const cur = await page.evaluate(() => {
    const dump = (sel: string, max = 12) => {
      const els = document.querySelectorAll(sel);
      const out: string[] = [];
      for (let i = 0; i < Math.min(els.length, max); i++) {
        const el = els[i] as HTMLElement;
        const cls = el.className?.toString().slice(0, 80) || "";
        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || "").trim().slice(0, 60);
        out.push(`<${tag} class="${cls}">${text}</${tag}>`);
      }
      return out;
    };
    return {
      vhero: dump(".vhero > *", 12),
      vheroNav: dump(".vhero-nav", 4),
      sbHead: dump(".sb-head", 2),
      sidebar: dump(".sidebar", 2),
      sbBody: dump(".sb-body", 3),
    };
  });

  await page.goto(
    "file:///<local-path-redacted>",
    { waitUntil: "domcontentloaded" }
  );
  await page.waitForTimeout(4000);
  const des = await page.evaluate(() => {
    const dump = (sel: string, max = 12) => {
      const els = document.querySelectorAll(sel);
      const out: string[] = [];
      for (let i = 0; i < Math.min(els.length, max); i++) {
        const el = els[i] as HTMLElement;
        const cls = el.className?.toString().slice(0, 80) || "";
        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || "").trim().slice(0, 60);
        out.push(`<${tag} class="${cls}">${text}</${tag}>`);
      }
      return out;
    };
    return {
      vhero: dump(".vhero > *", 12),
      vheroNav: dump(".vhero-nav", 4),
      sbHead: dump(".sb-head", 2),
      sidebar: dump(".sidebar", 2),
      sbBody: dump(".sb-body", 3),
    };
  });

  console.log("\n=== VHERO SIDEBAR DIFF ===");
  const keys = Array.from(new Set([...Object.keys(cur), ...Object.keys(des)]));
  for (const k of keys) {
    console.log(`\n[${k}] CUR:`);
    for (const s of cur[k] || []) console.log(`  ${s}`);
    console.log(`[${k}] DES:`);
    for (const s of des[k] || []) console.log(`  ${s}`);
  }
});
