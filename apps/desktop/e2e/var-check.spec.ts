import { test } from "@playwright/test";

test("check CSS variables", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  const vars = await page.evaluate(() => {
    const root = document.documentElement;
    const cs = getComputedStyle(root);
    return {
      "color-bg": cs.getPropertyValue("--color-bg"),
      "color-surface": cs.getPropertyValue("--color-surface"),
      "color-surface-2": cs.getPropertyValue("--color-surface-2"),
      "color-surface-3": cs.getPropertyValue("--color-surface-3"),
      "color-ink": cs.getPropertyValue("--color-ink"),
      "color-gold": cs.getPropertyValue("--color-gold"),
      "color-pos": cs.getPropertyValue("--color-pos"),
    };
  });
  console.log("=== CURRENT CSS VARS (on :root) ===\n" + JSON.stringify(vars, null, 2));

  await page.goto("file:///<local-path-redacted>", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const desVars = await page.evaluate(() => {
    const root = document.documentElement;
    const cs = getComputedStyle(root);
    return {
      "bg": cs.getPropertyValue("--bg"),
      "surface": cs.getPropertyValue("--surface"),
      "surface-2": cs.getPropertyValue("--surface-2"),
      "surface-3": cs.getPropertyValue("--surface-3"),
      "ink": cs.getPropertyValue("--ink"),
      "gold": cs.getPropertyValue("--gold"),
      "pos": cs.getPropertyValue("--pos"),
    };
  });
  console.log("=== DESIGN CSS VARS (on :root) ===\n" + JSON.stringify(desVars, null, 2));
});
