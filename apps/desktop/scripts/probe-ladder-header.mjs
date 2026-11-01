// 只读诊断:/ladder 在真后端下的 date 与卡片头右槽渲染是否一致
import { chromium } from "playwright";

const URL = process.env.PROBE_BASE ?? "http://localhost:3001/watch/ashare-board";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
const seen = [];
page.on("response", async (r) => {
  if (!/\/api\/v1\/ashare\/ladder/.test(r.url())) return;
  try {
    const body = await r.json();
    seen.push({ url: r.url(), http: r.status(), date: body?.data?.date ?? null, status: body?.data?.status ?? null, today: body?.data?.today ?? null });
  } catch (e) {
    seen.push({ url: r.url(), err: String(e) });
  }
});
await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 120_000 });
await page.waitForTimeout(30_000);
const heads = await page.evaluate(() => {
  return [...document.querySelectorAll(".rp-card")].map((card) => {
    const titleEl = card.querySelector('[class*="text-[10px]"]');
    const rightEl = card.querySelector('[class*="text-[9px]"]');
    return { title: (titleEl?.textContent ?? "").trim().slice(0, 40), right: (rightEl?.textContent ?? "").trim().slice(0, 60) };
  });
});
console.log(JSON.stringify({ ladderResponses: seen, cardHeaders: heads }, null, 2));
await browser.close();
