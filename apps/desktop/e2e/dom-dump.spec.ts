import { test } from "@playwright/test";

test("dump current DOM structure", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://localhost:3000/watch/kf", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  const tree = await page.evaluate(() => {
    function walk(node: Element | null, depth: number, maxDepth: number): string {
      if (!node || depth > maxDepth) return "";
      const el = node as Element;
      const tag = el.tagName.toLowerCase();
      const cls = (el.getAttribute("class") || "").slice(0, 80);
      const id = el.getAttribute("id") ? `#${el.getAttribute("id")}` : "";
      const rect = el.getBoundingClientRect();
      const indent = "  ".repeat(depth);
      const size = ` [${Math.round(rect.width)}x${Math.round(rect.height)} @${Math.round(rect.left)},${Math.round(rect.top)}]`;
      let out = `${indent}<${tag}${id}${cls ? ` class="${cls}"` : ""}>${size}\n`;
      if (depth < maxDepth) {
        for (const child of Array.from(el.children)) {
          out += walk(child, depth + 1, maxDepth);
        }
      }
      return out;
    }
    return walk(document.querySelector(".app-shell") ?? document.body, 0, 4);
  });

  console.log("=== CURRENT /watch/kf DOM (4 levels) ===\n" + tree);
});

test("dump design draft DOM structure", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("file:///<local-path-redacted>", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  const tree = await page.evaluate(() => {
    function walk(node: Element | null, depth: number, maxDepth: number): string {
      if (!node || depth > maxDepth) return "";
      const el = node as Element;
      const tag = el.tagName.toLowerCase();
      const cls = (el.getAttribute("class") || "").slice(0, 80);
      const id = el.getAttribute("id") ? `#${el.getAttribute("id")}` : "";
      const rect = el.getBoundingClientRect();
      const indent = "  ".repeat(depth);
      const size = ` [${Math.round(rect.width)}x${Math.round(rect.height)} @${Math.round(rect.left)},${Math.round(rect.top)}]`;
      let out = `${indent}<${tag}${id}${cls ? ` class="${cls}"` : ""}>${size}\n`;
      if (depth < maxDepth) {
        for (const child of Array.from(el.children)) {
          out += walk(child, depth + 1, maxDepth);
        }
      }
      return out;
    }
    return walk(document.querySelector(".app") ?? document.body, 0, 4);
  });

  console.log("=== DESIGN DRAFT DOM (4 levels) ===\n" + tree);
});
