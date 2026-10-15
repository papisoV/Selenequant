import { test } from "@playwright/test";

test("card heights at 1024x768", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  
  const info = await page.evaluate(() => {
    const rp = document.querySelector(".right-panel") as HTMLElement;
    const cards = Array.from(document.querySelectorAll(".rp-card")) as HTMLElement[];
    return {
      rpClientH: rp?.clientHeight,
      rpScrollH: rp?.scrollHeight,
      cards: cards.map((c, i) => {
        const r = c.getBoundingClientRect();
        const head = c.querySelector(".rp-card-head");
        const body = c.querySelector(".rp-card-body");
        const title = c.querySelector(".rp-card-title")?.textContent?.trim().slice(0, 20);
        return {
          idx: i,
          title,
          folded: c.classList.contains("folded"),
          h: Math.round(r.height),
          headH: head ? Math.round(head.getBoundingClientRect().height) : 0,
          bodyH: body ? Math.round(body.getBoundingClientRect().height) : 0,
        };
      }),
    };
  });
  
  console.log("\n=== CARDS at 1024x768 ===");
  console.log(`right-panel clientH=${info.rpClientH} scrollH=${info.rpScrollH}`);
  let totalH = 0;
  for (const c of info.cards) {
    console.log(`  [${c.idx}] ${c.title} folded=${c.folded} h=${c.h} head=${c.headH} body=${c.bodyH}`);
    totalH += c.h;
  }
  console.log(`Total cards height: ${totalH}px (gap ${info.rpScrollH - info.rpClientH}px overflow)`);
});
