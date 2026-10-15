import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:3013/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
const hits = await page.evaluate(() => {
  const out = [];
  for (const el of document.body.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const text = Array.from(el.childNodes ?? [])
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent?.trim() ?? "")
      .join("")
      .slice(0, 50);
    const c = cs.color;
    const bg = cs.backgroundColor;
    const flagText = c === "rgb(34, 37, 40)" || c === "rgb(73, 77, 81)";
    const flagBg = bg === "rgb(34, 37, 40)" || bg === "rgb(73, 77, 81)";
    if (flagText || flagBg) {
      out.push({
        tag: el.tagName.toLowerCase(),
        cls: String(el.className).slice(0, 140),
        text,
        color: c,
        bg,
        ariaHidden: el.getAttribute("aria-hidden"),
        y: Math.round(rect.top + window.scrollY),
      });
    }
  }
  return out;
});
await browser.close();
console.log(JSON.stringify(hits, null, 1));
