import { test, expect } from "@playwright/test";

// kf 换标的残留/嵌合K线回归（2026-10-15）。
//
// 症状：切换标的（或切换周期）后，图左上角 overlay 已经写着新标的，但十字线
// tooltip 仍在返回**旧标的**的价格，持续约 4 秒才收敛。
//
// 根因：pickRenderPath 只比 (length, lastTs)，而交易所对任何标的/周期都返回
// "最近 N 根、末根对齐当前时刻" —— 实测 BTC 1h 与 ETH 1h 完全同形
// (n=1500, 首 1786669200000, 末 1792065600000)。换标的因此被判为 skip，
// setData 永不执行，画布与 rowsMapRef 都留在旧标的。
//
// 本测试的判据是**语义量**：overlay 写的标的身分 vs tooltip 返回的价格量级。
// 不做像素指纹（它分不出"缩放/平移"与"数据没换"）。
//
// ⚠️ 抖动（jitter）不可删：LWC 在 mouse.move 落在**未变坐标**时不重新派发
// crosshair 事件，tooltip 会冻结 → 探针读到上一帧的值 → 产出**假残留**。
// 每次采样前先移到偏移点再回目标点，强制重新派发。

const BASE = process.env.E2E_BASE ?? `http://localhost:${process.env.E2E_PORT ?? "3002"}`;

/** 价格带：粗略量级即可，用来判"这价属于哪个标的"。
 *  取宽区间，避免行情波动导致的假阳性。 */
const BAND: Record<string, [number, number]> = {
  BTC: [20_000, 500_000],
  ETH: [500, 20_000],
};

interface Frame {
  ms: number;
  ov: string | null;
  close: number | null;
}

/** 建好图、取到 canvas 框、并完成仪器自检的页面句柄。 */
async function openChart(page: import("@playwright/test").Page) {
  await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded", timeout: 90_000 });
  await page.waitForSelector(".chart-body canvas", { timeout: 60_000 });

  const box = await page.evaluate(() => {
    const c = document.querySelector(".chart-body canvas") as HTMLCanvasElement | null;
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
  });
  expect(box, "应能取到主图 canvas 的包围盒").not.toBeNull();

  let jig = 0;
  const hover = async (fx: number): Promise<{ ov: string | null; close: number | null }> => {
    const x = box!.x + box!.w * fx;
    const y = box!.y + box!.h * 0.30;
    jig = (jig + 1) % 2;
    await page.mouse.move(x + (jig ? 7 : -7), y + (jig ? 4 : -4));
    await page.waitForTimeout(40);
    await page.mouse.move(x, y);
    await page.waitForTimeout(160);
    return page.evaluate(() => {
      const tip = document.querySelector(".kf-cross-tip");
      const ov = document.querySelector(".kline-overlay");
      const txt = tip ? (tip.textContent || "").replace(/\s+/g, " ").trim() : "";
      const nums = (txt.match(/-?\d[\d,]*\.?\d*/g) || []).map((s) => Number(s.replace(/,/g, "")));
      return {
        ov: ov ? (ov.textContent || "").replace(/\s+/g, " ").trim() : null,
        // nums[9] = C 行的收盘价（前 6 个是日期/时间的数字）。用 index 取而非
        // 文本匹配，因为时间戳里也有数字。
        close: nums.length >= 10 ? nums[9] : null,
      };
    });
  };

  // 等首帧**真的**绘出来 —— 固定等待不可靠：冷 dev 编译(kf-chart 刚改过会触发
  // Turbopack 重编译)期间页面可能整体重置,此时点 ⌘K 选标的会被重载吃掉,
  // 采样全程读回默认的 BTC,产出"换了标的但 overlay 没变"的假失败
  // (2026-10-15 首次 GREEN 就是这么挂的)。判据必须是"tooltip 真读到价"。
  let ready = false;
  for (let i = 0; i < 60; i++) {
    const s = await hover(0.30);
    if (s.close != null && s.ov) {
      ready = true;
      break;
    }
    await page.waitForTimeout(1_000);
  }
  expect(ready, "图表应在 60s 内绘出首帧（overlay 与 tooltip 均有内容）").toBe(true);
  // 首帧绘出后数据窗口可能还在补齐,给它落定的时间,避免把加载过程当残留。
  await page.waitForTimeout(3_000);
  return { hover };
}

/** ⌘K 选标的。返回是否成功选中。 */
async function pickSymbol(page: import("@playwright/test").Page, query: string, badge: string) {
  await page.keyboard.press("Control+k");
  await page.waitForSelector('input[role="combobox"]', { timeout: 20_000 });
  await page.fill('input[role="combobox"]', query);
  await page.waitForTimeout(2_200);
  const rows = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[data-testid="symbol-row"]')).map((r, i) => ({
      i,
      text: (r.textContent || "").replace(/\s+/g, " ").trim(),
    })),
  );
  const hit = rows.find((r) => r.text.includes(badge));
  if (!hit) {
    await page.keyboard.press("Escape");
    return false;
  }
  await page.waitForTimeout(250); // 等 ⌘K 收起动画走完再点，否则点到遮罩
  const handles = await page.$$('[data-testid="symbol-row"]');
  await handles[hit.i].click();
  return true;
}

/** 换标的后逐帧采样，直到窗口结束。 */
async function sampleAfterSwitch(
  page: import("@playwright/test").Page,
  hover: (fx: number) => Promise<{ ov: string | null; close: number | null }>,
  frames = 34,
  gapMs = 120,
): Promise<Frame[]> {
  const t0 = Date.now();
  const out: Frame[] = [];
  for (let i = 0; i < frames; i++) {
    const s = await hover(0.30);
    out.push({ ms: Date.now() - t0, ...s });
    await page.waitForTimeout(gapMs);
  }
  return out;
}

test("换标的:overlay 写新标的后,tooltip 不得再返回旧标的的价格", async ({ page }) => {
  test.setTimeout(240_000);
  const { hover } = await openChart(page);

  // ── 仪器自检：抖动生效后,同点(近似)稳定、异点可分 ──────────────────────
  // 不自检就采样的教训(2026-10-15)：读到的是常量时先疑仪器,不是先下结论。
  const a1 = await hover(0.55);
  const a2 = await hover(0.55);
  const b1 = await hover(0.30);
  expect(a1.close, "自检:同一点两次采样都应读到价格").not.toBeNull();
  expect(b1.close, "自检:另一点也应读到价格").not.toBeNull();
  expect(a1.close, "自检:同一点连续采样应稳定(否则是仪器漂移,不是被测现象)").toBe(a2.close);
  expect(a1.close, "自检:不同点应可区分(否则读的是常量,无分辨力)").not.toBe(b1.close);

  // 起点确认是 BTC
  expect(a1.ov ?? "", "起始图应为 BTC").toContain("BTC");

  // ── 切到 ETH ───────────────────────────────────────────────────────────
  expect(await pickSymbol(page, "ETH", "Binance"), "应能选中 ETH/Binance").toBe(true);

  // 先确认这一次切换**真的生效了**再采样。这一步是必须的：next dev 在源码
  // 变动后重编译会让页面整体重载回默认的 BTC，此时采样会读到"overlay 从没
  // 变成 ETH"，看起来像断言失败、其实是环境把实验对象换掉了
  // （2026-10-15 首次 RED 就栽在这，读到 0 个 ETH 帧）。
  // 轮询等待并把它与"残留"区分开：等不到 = 前提不成立，不是被测现象。
  let pickedEth = false;
  for (let i = 0; i < 40; i++) {
    const s = await hover(0.30);
    if ((s.ov ?? "").includes("ETH")) {
      pickedEth = true;
      break;
    }
    await page.waitForTimeout(500);
  }
  expect(
    pickedEth,
    "切换未生效（overlay 始终未显示 ETH）—— 环境问题(如 dev 重编译把页面重置回 BTC)，非被测残留",
  ).toBe(true);

  // ── 逐帧采样 ───────────────────────────────────────────────────────────
  const frames = await sampleAfterSwitch(page, hover);

  // overlay 读 React state、应始终是 ETH；tooltip 读 rowsMapRef、才是慢的那个。
  const ethFrames = frames.filter((f) => (f.ov ?? "").includes("ETH"));
  expect(ethFrames.length, "切换生效后 overlay 应全程显示 ETH").toBeGreaterThan(0);

  // 契约:overlay 一旦写着 ETH,同一帧的 tooltip 就不能再返回 BTC 量级的价。
  const [btcLo] = BAND.BTC;
  const [ethLo, ethHi] = BAND.ETH;
  const mixed = ethFrames.filter((f) => f.close != null && f.close >= btcLo);

  // 失败时把证据打出来(帧序号/时间/overlay/tooltip close),便于定位窗口长度。
  if (mixed.length > 0) {
    const detail = mixed
      .map((f) => `  t=${f.ms}ms ov="${(f.ov ?? "").slice(0, 14)}" close=${f.close}`)
      .join("\n");
    throw new Error(
      `换标的后有 ${mixed.length}/${ethFrames.length} 帧 overlay 已是 ETH 但 tooltip 仍返回 BTC 量级价格(残留):\n${detail}`,
    );
  }

  // 反向对照:采样必须真的读到了 ETH 的价格带,否则整个断言是空转的假绿。
  // (只断言"没有 BTC 价"是不够的 —— 若 tooltip 全程为 null 也会通过。)
  const settled = frames[frames.length - 1];
  expect(settled.close, "末帧应读到价格").not.toBeNull();
  expect(
    settled.close!,
    `末帧应收敛到 ETH 价格带(${ethLo}..${ethHi}),实际 ${settled.close}`,
  ).toBeGreaterThan(ethLo);
  expect(settled.close!, "末帧不应仍在 BTC 价格带").toBeLessThan(ethHi);
});
