// Color/style mechanical audit for landing page (DOM computed styles only, no screenshots)
// Usage: node scripts/audit-colors.mjs [url] [outfile]
import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const url = process.argv[2] ?? "http://localhost:3013/";
const out = process.argv[3] ?? "scratch/audit-colors.json";

function parseRgb(s) {
  if (!s) return null;
  const m = s.match(/rgba?\(([^)]+)\)/);
  if (!m) return null;
  const parts = m[1].split(",").map((x) => parseFloat(x));
  if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return null;
  const [r, g, b, a = 1] = parts;
  return { r, g, b, a };
}

function lum({ r, g, b }) {
  const f = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fg, bg) {
  const l1 = lum(fg);
  const l2 = lum(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

// composite rgba over solid bg
function over(fg, bg) {
  const a = fg.a;
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1,
  };
}

function rgbStr(c) {
  const r = Math.round(c.r);
  const g = Math.round(c.g);
  const b = Math.round(c.b);
  return `rgb(${r},${g},${b})`;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const data = await page.evaluate(() => {
  const bodyBg = getComputedStyle(document.body).backgroundColor;
  const els = Array.from(document.body.querySelectorAll("*"));
  const rows = [];
  for (const el of els) {
    const cs = getComputedStyle(el);
    const text = (el.childNodes ? Array.from(el.childNodes) : [])
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent?.trim() ?? "")
      .join("");
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    rows.push({
      tag: el.tagName.toLowerCase(),
      cls: typeof el.className === "string" ? el.className.slice(0, 160) : "",
      text: text.slice(0, 40),
      color: cs.color,
      bg: cs.backgroundColor,
      borderColor: cs.borderTopColor,
      borderStyle: cs.borderTopStyle,
      borderWidth: cs.borderTopWidth,
      radius: cs.borderTopLeftRadius,
      fontFamily: cs.fontFamily.slice(0, 80),
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
      letterSpacing: cs.letterSpacing,
      hasText: text.length > 0,
      opacity: cs.opacity,
    });
  }
  // section boundaries for per-section rollup
  const sections = Array.from(document.querySelectorAll("section, header, footer")).map(
    (s, i) => ({
      i,
      id: s.id || s.className.split(" ").slice(0, 3).join(" "),
      text: (s.textContent ?? "").slice(0, 60),
    }),
  );
  return { bodyBg, rows, sections };
});

await browser.close();

const bgSolid = parseRgb(data.bodyBg) ?? { r: 4, g: 7, b: 11, a: 1 };

// resolve effective background per element: walk not possible post-hoc; approximate with page-level map
// Instead re-walk with ancestor resolution in a second pass is costly; approximate:
// elements with solid bg become "panels"; assume text sits on nearest panel bg = body bg (landing is flat)
// -- acceptable for palette audit; contrast numbers flagged accordingly.

const textColors = {};
const bgColors = {};
const borderColors = {};
const radii = {};
const fontSizes = {};
const fontFamilies = {};
const contrastPairs = {};

for (const r of data.rows) {
  if (r.hasText) {
    const c = parseRgb(r.color);
    if (c) {
      const key = rgbStr(c.a < 1 ? over(c, bgSolid) : c);
      textColors[key] = (textColors[key] ?? 0) + 1;
      if (c) {
        const eff = c.a < 1 ? over(c, bgSolid) : c;
        const ratio = contrast(eff, bgSolid);
        const bucket = ratio >= 7 ? "AAA" : ratio >= 4.5 ? "AA" : ratio >= 3 ? "AA-large" : "FAIL";
        contrastPairs[`${key} on ${rgbStr(bgSolid)} = ${ratio.toFixed(2)} [${bucket}]`] =
          (contrastPairs[`${key} on ${rgbStr(bgSolid)} = ${ratio.toFixed(2)} [${bucket}]`] ?? 0) + 1;
      }
    }
    const fs = r.fontSize;
    fontSizes[fs] = (fontSizes[fs] ?? 0) + 1;
    const ff = r.fontFamily.split(",")[0];
    fontFamilies[ff] = (fontFamilies[ff] ?? 0) + 1;
  }
  const b = parseRgb(r.bg);
  if (b && b.a > 0.02) {
    const key = rgbStr(b.a < 1 ? over(b, bgSolid) : b);
    bgColors[key] = (bgColors[key] ?? 0) + 1;
  }
  if (r.borderStyle !== "none" && parseFloat(r.borderWidth) > 0) {
    const bc = parseRgb(r.borderColor);
    if (bc) {
      const key = rgbStr(bc.a < 1 ? over(bc, bgSolid) : bc);
      borderColors[key] = (borderColors[key] ?? 0) + 1;
    }
  }
  const rad = r.radius;
  radii[rad] = (radii[rad] ?? 0) + 1;
}

function sorted(obj) {
  return Object.entries(obj).sort((a, b) => b[1] - a[1]);
}

const summary = {
  bodyBg: data.bodyBg,
  textColors: sorted(textColors),
  bgColors: sorted(bgColors),
  borderColors: sorted(borderColors),
  radii: sorted(radii),
  fontSizes: sorted(fontSizes),
  fontFamilies: sorted(fontFamilies),
  contrastPairs: sorted(contrastPairs),
  totalElements: data.rows.length,
};

writeFileSync(out, JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
