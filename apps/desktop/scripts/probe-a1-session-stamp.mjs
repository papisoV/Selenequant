// A1 渲染取证:看盘面上的会话时标(SessionStamp)。
// 只读:不点任何写操作、不发 POST。
//
// 要证明三件事:
//   1. 时标真的渲染出来了(settled → 「截至 X」),不是只过了单测;
//   2. 时标是中性色(text-ink-4),没有吃掉涨跌/金色的色相;
//   3. 新鲜度徽章(AshareFreshness)与它并排共处,各说各的。
import { chromium } from "playwright";

const BASE = process.env.PROBE_BASE ?? "http://localhost:3001";
const URL = `${BASE}/watch/ashare-board`;
const SETTLE_MS = 30_000;
const SHOT = "<local-path-redacted>";

const page = await (await chromium.launch()).newPage({ viewport: { width: 1600, height: 1100 } });
// networkidle 永不触发:看盘面本身就在轮询。用 domcontentloaded + 固定等待。
await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 90_000 });
await page.waitForTimeout(SETTLE_MS);

const result = await page.evaluate(() => {
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = (n.textContent || "").trim();
    if (/^(截至\s|盘中暂定)/.test(t)) {
      const el = n.parentElement;
      texts.push({
        text: t,
        cls: el?.className ?? "",
        color: el ? getComputedStyle(el).color : null,
        inBand: !!el?.closest('[data-testid="ashare-overview-band"]'),
        inDeviation: !!el?.closest('[data-testid="ashare-deviation-panel"]'),
      });
    }
  }
  return texts;
});

await page.screenshot({ path: SHOT, fullPage: false });
console.log(JSON.stringify({ count: result.length, stamps: result, shot: SHOT }, null, 2));
await page.context().browser().close();
