/* 导航性能实测 v2:每次导航从 /watch/kf 出发,点 sidebar 目标,测量 URL 变化 + 稳定时间。
   修正 v1 bug:不在目标页上点下一个链接,而是先回 kf 再点。 */
import { chromium } from "playwright";

const TARGETS = (process.argv[2] ?? "gex,liqmap,agg").split(",").map((s) => s.trim());
const ORIGIN = "http://localhost:3000";
const SCENE = "watch";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
page.on("pageerror", (e) => console.log(`[pageerror] ${e.message.slice(0, 160)}`));

console.log(`== 导航性能实测 v2 (origin=${ORIGIN}) ==`);
console.log(`目标: ${TARGETS.join(", ")}`);

// 首屏 kf,建立 WS + 缓存
await page.goto(`${ORIGIN}/${SCENE}/kf`, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForSelector('a[href="/watch/kf"]', { timeout: 20000 });
await sleep(3000);
console.log("首屏 /watch/kf 就绪。\n");

for (let i = 0; i < TARGETS.length; i++) {
  const slug = TARGETS[i];
  const t0 = Date.now();
  await page.click(`a[href="/${SCENE}/${slug}"]`, { timeout: 10000 });
  const clickDone = Date.now() - t0;
  await page.waitForURL(`**/${SCENE}/${slug}`, { timeout: 15000 });
  const urlDone = Date.now() - t0;
  // 等页面内容稳定:目标页出现 (通过 sidebar active 状态或页内任意内容)
  await sleep(1500);
  const stableDone = Date.now() - t0;
  const active = await page.evaluate((s) => {
    const el = document.querySelector(`a[href="/watch/${s}"]`);
    return el?.className?.includes("active") ?? false;
  }, slug);
  console.log(`--- ${slug} ---`);
  console.log(`  点击→URL变化: ${clickDone}ms | URL→稳定: ${stableDone - urlDone}ms | 总: ${stableDone}ms | active=${active}`);
  // 回 kf 重置:用 SPA 导航而非整页 reload,排除首屏重建噪声
  await page.click('a[href="/watch/kf"]', { timeout: 10000 });
  await page.waitForURL("**/watch/kf", { timeout: 15000 });
  await sleep(2000);
}

await browser.close();
console.log("\n== 完成 ==");
