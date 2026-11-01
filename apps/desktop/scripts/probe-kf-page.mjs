// /rsch 研究中心探查(等 429 窗口过去;研究中心=landing 叙事同源的库数据)
import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const api = [];
page.on("response", (r) => {
  const u = r.url();
  if (u.includes("api/v1") && r.request().resourceType() === "fetch") {
    api.push(`${r.status()} ${u.replace(/^https?:\/\/[^/]+/, "").slice(0, 60)}`);
  }
});
await page.goto("http://localhost:3013/rsch", { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(15000);

const info = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    textLen: text.length,
    sample: text.replace(/\s+/g, " ").slice(200, 600),
    hasData: /\d{2,}/.test(text.replace(/SEL|ENE|SPAC|QUANT|v0|\d{2}:\d{2}/g, "")),
    tables: document.querySelectorAll("table tbody tr").length,
    canvas: document.querySelectorAll("canvas").length,
  };
});
console.log("api:", api.length);
api.slice(0, 10).forEach((h) => console.log("  ", h));
console.log(JSON.stringify(info, null, 1));
await browser.close();
