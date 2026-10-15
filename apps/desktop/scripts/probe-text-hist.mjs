import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:3013/", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
const hist = await page.evaluate(() => {
  const out = {};
  for (const el of document.body.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const text = Array.from(el.childNodes ?? [])
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent?.trim() ?? "")
      .join("");
    if (!text) continue;
    // effective color: parse rgb(a)
    const m = cs.color.match(/rgba?\(([^)]+)\)/);
    if (!m) continue;
    const p = m[1].split(",").map(Number);
    const key = `${p[0]},${p[1]},${p[2]}@${p[3] ?? 1}`;
    out[key] ??= { n: 0, sample: "" };
    out[key].n++;
    if (out[key].n <= 3)
      out[key].sample = `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 80)} :: ${text.slice(0, 30)}`;
  }
  return out;
});
await browser.close();
const entries = Object.entries(hist).sort((a, b) => b[1].n - a[1].n);
for (const [k, v] of entries) console.log(`${k}  x${v.n}\n   ${v.sample}`);
