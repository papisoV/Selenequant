import { chromium } from "playwright";

// R5d 七色语义探针:断言七种语义色都以可见形式出现在渲染 DOM 中
const URL = process.argv[2] || "http://localhost:3013/";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(URL, { waitUntil: "networkidle" });
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(1200);

const probe = await page.evaluate(() => {
  const wanted = [
    "text-research", "bg-research", "border-research",
    "text-memory", "bg-memory", "border-memory",
    "text-pos", "bg-pos", "text-neg", "bg-neg",
    "text-gold", "bg-gold", "bg-primary", "text-accent",
  ];
  const counts = {};
  for (const cls of wanted) {
    counts[cls] = document.querySelectorAll(`[class*="${cls}"]`).length;
  }
  // SVG 语义色(节点/线/粒子)
  const svg = {
    cyan: document.querySelectorAll('[fill="#22d3ee"], [stroke*="34,211,238"], [fill*="34,211,238"]').length,
    violet: document.querySelectorAll('[fill="#a78bfa"], [stroke*="167,139,250"], [fill*="167,139,250"]').length,
    gold: document.querySelectorAll('[stroke*="227,182,80"], [fill*="227,182,80"]').length,
    flowDash: document.querySelectorAll(".lg-flow").length,
    rgraph: document.querySelectorAll(".lp-rgraph").length,
  };
  return { counts, svg };
});

let fail = 0;
for (const [k, v] of Object.entries(probe.counts)) {
  const tag = v > 0 ? "ok" : "MISSING";
  if (v === 0) fail++;
  console.log(`${tag} ${k} = ${v}`);
}
for (const [k, v] of Object.entries(probe.svg)) {
  const tag = v > 0 ? "ok" : "MISSING";
  if (v === 0) fail++;
  console.log(`${tag} svg:${k} = ${v}`);
}
console.log(fail === 0 ? "== 7-COLOR PRESENCE PASS ==" : `== ${fail} MISSING ==`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
