import { test, expect } from "@playwright/test";

// kf 指标开关白屏复现 v2:只点 MA(用户复现路径),300 轮跨 REST revalidate,
// 每轮采样 canvas alpha 像素判定空图(禁截图纪律:纯 DOM/canvas 数据断言)。

async function canvasStats(page: import("@playwright/test").Page) {
  return page.evaluate(() => {
    const canvases = Array.from(document.querySelectorAll(".chart-body canvas")) as HTMLCanvasElement[];
    return canvases.map((c, i) => {
      try {
        const ctx = c.getContext("2d");
        if (!ctx || c.width === 0 || c.height === 0) return { i, w: c.width, h: c.height, painted: -1 };
        // 稀疏网格采样 alpha
        const step = Math.max(8, Math.floor(c.width / 40));
        let total = 0, painted = 0;
        for (let x = 0; x < c.width; x += step) {
          for (let y = 0; y < c.height; y += step) {
            total++;
            if (ctx.getImageData(x, y, 1, 1).data[3] > 0) painted++;
          }
        }
        return { i, w: c.width, h: c.height, painted, total };
      } catch (e) {
        return { i, w: c.width, h: c.height, painted: -2, err: String(e).slice(0, 80) };
      }
    });
  });
}

test("MA toggle storm: chart never goes blank at pixel level", async ({ page }) => {
  test.setTimeout(180_000);
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text().slice(0, 400));
  });
  page.on("pageerror", (err) => pageErrors.push(String(err).slice(0, 600)));

  await page.goto("http://localhost:3002/watch/kf", { waitUntil: "domcontentloaded" });
  // 切 1m:WS 注入 + 10s REST 轮询最密集,竞态窗口最大
  await page.waitForSelector(".chart-body canvas", { timeout: 30_000 });

  // 必须等首帧真正绘出再打风暴。固定预热(曾用 4s)会早于首帧:
  // KLINE_FETCH_LIMIT=1500 全量首拉 + LWC 首绘,冷 dev 编译下实测 2-8s 不等;
  // 早于首帧采样会把"数据还没到"误判成 toggle 白屏(2026-10-02 合并复验踩到)。
  // 判据取主图 canvas[0]:canvas[1] 是 LWC overlay 层,恒为 0 像素不可作依据。
  let readyMs = -1;
  const readyT0 = Date.now();
  for (let i = 0; i < 120; i++) {
    const s = await canvasStats(page);
    if ((s[0]?.painted ?? 0) > 0) {
      readyMs = Date.now() - readyT0;
      break;
    }
    await page.waitForTimeout(500);
  }
  console.log(`RENDER READY after ${readyMs}ms`);
  expect(readyMs, "图表应在 60s 内绘出首帧").toBeGreaterThan(0);

  const maBtn = page.locator(".chart-tool", { hasText: /^MA$/ });
  await expect(maBtn).toBeVisible();

  let blankAt = -1;
  let persistentBlank = false;
  const rounds = 300;
  for (let i = 0; i < rounds; i++) {
    await maBtn.click();
    await page.waitForTimeout(40);
    if (i % 5 === 4) {
      const stats = await canvasStats(page);
      // 判定只看主图 canvas[0]：canvas[1] 是 overlay 层恒为 0，纳入 every 会稀释语义
      const mainBlank = (stats[0]?.painted ?? 0) === 0;
      const anyBroken = stats.some((s) => s.painted === -2);
      if ((mainBlank || anyBroken) && blankAt < 0) {
        blankAt = i;
        console.log(`BLANK at round ${i}: stats=${JSON.stringify(stats)}`);
        // 空白是否持久:等 600ms 再采样(修复后应自愈;修复前永久锁死)
        await page.waitForTimeout(600);
        const re = await canvasStats(page);
        const stillBlank = (re[0]?.painted ?? 0) === 0;
        persistentBlank = stillBlank;
        console.log(`after 600ms: stillBlank=${stillBlank} main=${JSON.stringify(re.slice(0, 2))}`);
        console.log(`consoleErrors so far: ${JSON.stringify(consoleErrors.slice(0, 5))}`);
        console.log(`pageErrors so far: ${JSON.stringify(pageErrors.slice(0, 5))}`);
        break;
      }
    }
  }

  // 汇总终态
  const finalStats = await canvasStats(page);
  console.log(`RESULT: rounds=${blankAt >= 0 ? blankAt + 1 : rounds} blankAt=${blankAt} persistentBlank=${persistentBlank}`);
  console.log(`finalStats=${JSON.stringify(finalStats)}`);
  console.log(`pageErrors(${pageErrors.length}):`);
  for (const e of pageErrors.slice(0, 8)) console.log(`  - ${e}`);
  const realErrors = consoleErrors.filter((e) => !e.includes("Download the React DevTools"));
  console.log(`consoleErrors(${realErrors.length}):`);
  for (const e of realErrors.slice(0, 8)) console.log(`  - ${e}`);

  // 判定标准:持久白屏 = bug(修复前永久锁死);瞬态空白 <600ms 自愈 = LWC 单帧重绘窗口,可接受
  expect(persistentBlank, "chart went PERSISTENTLY blank during MA toggle storm").toBe(false);
  expect(pageErrors, "uncaught page errors").toHaveLength(0);
});
