import { test } from "@playwright/test";

test("pixel sample comparison", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });

  const sample = async (label: string) => {
    return await page.evaluate(() => {
      const samples: Record<string, string> = {};
      const pts: Array<[string, number, number]> = [
        ["body-bg", 10, 450],
        ["vhero-bg", 28, 450],
        ["sidebar-bg", 120, 450],
        ["chartMain-bg", 400, 450],
        ["rightPanel-bg", 1300, 200],
        ["drawer-bg", 400, 820],
        ["feedBar-bg", 400, 884],
        ["vhero-top", 28, 20],
        ["sidebar-top", 120, 20],
        ["symbolBar-top", 400, 20],
        ["chart-toolbar", 400, 60],
        ["right-panel-card", 1300, 120],
      ];
      for (const [name, x, y] of pts) {
        const el = document.elementFromPoint(x, y) as HTMLElement | null;
        if (el) {
          const cs = getComputedStyle(el);
          samples[name] = `${cs.backgroundColor} | ${cs.color.slice(0, 30)} | ${el.tagName}.${el.className.slice(0, 40)}`;
        }
      }
      return samples;
    });
  };

  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  const cur = await sample("cur");

  await page.goto(
    "file:///<local-path-redacted>",
    { waitUntil: "networkidle" }
  );
  await page.waitForTimeout(2500);
  const des = await sample("des");

  const allKeys = Array.from(new Set([...Object.keys(cur), ...Object.keys(des)]));
  console.log("\n=== PIXEL SAMPLE COMPARISON ===");
  for (const k of allKeys) {
    console.log(`\n[${k}]`);
    console.log(`  cur: ${cur[k] ?? "—"}`);
    console.log(`  des: ${des[k] ?? "—"}`);
  }
});
