import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// @axe-core/playwright 未安装 → 直接注入 axe-core 源码到页面执行
// (playwright 以 CJS 转译 spec,禁 import.meta;用 project-root 相对路径取包)
const axeSource = readFileSync(resolve("node_modules/axe-core/axe.min.js"), "utf8");
type Axe = { run: (ctx: unknown, opts?: unknown) => Promise<{ violations: Array<{ id: string; impact?: string | null; nodes: unknown[] }> }> };

// S6 活体验收 — open-source-reference 吸收批 REVIEW 哨兵 badge(1ff7b7cd)
// 目标:在真实 Chromium 里渲染 /system/agent-trading-workspace,断言:
//   1. is_review:true 的教训渲染成 warn badge(文本"评级读不出·需人看")
//   2. is_review:false 的教训渲染成纯 rating 文本(不受影响)
//   3. warn badge 的实测对比度(computed color vs 有效背景)≥ 4.5:1 (AA 正文)
//   4. 整页无 axe critical/serious 违规
//   5. 落截图作人工视觉核验
//
// 纪律:数据全 mock(不碰真后端);不点任何 destructive 控件;不固定 sleep。

const PORT = process.env.E2E_PORT ?? "3011";
const BASE = `http://localhost:${PORT}`;

async function mockHealth(page: Page) {
  await page.route("**/api/v1/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) }),
  );
}

async function mockWorkspace(page: Page) {
  // 交易工作台本体 — 空持仓即可(本 spec 只验决策教训 badge)
  await page.route("**/api/v1/agent/trading/workspace*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ positions: [], pending_orders: [], active_strategies: [], account_summary: {} }),
    }),
  );
}

async function mockLessons(page: Page) {
  await page.route("**/api/v1/agent/decision-lessons*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          deployment_id: "dep-1",
          strategy_id: "bb_v3",
          symbols: ["BTCUSDT"],
          entry_date: "2026-10-01",
          rating: "REVIEW",
          is_review: true,
          status: "filled",
          reflection_text: "评级读不出,需人看或重跑",
        },
        {
          deployment_id: "dep-2",
          strategy_id: "rsi_ma_delta",
          symbols: ["ETHUSDT"],
          entry_date: "2026-10-02",
          rating: "Buy",
          is_review: false,
          status: "pending",
          reflection_text: "常规评级",
        },
      ]),
    }),
  );
}

// rgb(r,g,b) 解析(剥离 oklab 等前的旧路径已被 canvas 解析取代)
function srgbToLin(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function relLum([r, g, b]: [number, number, number]): number {
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function contrast(fg: [number, number, number], bg: [number, number, number]): number {
  const l1 = relLum(fg), l2 = relLum(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

async function gotoWorkspace(page: Page) {
  await page.goto(`${BASE}/system/agent-trading-workspace`, { waitUntil: "domcontentloaded" });
  // 等 badge 出现 = hydration + SWR 数据到位(禁固定 sleep)
  await page.waitForSelector("text=评级读不出", { timeout: 20_000 });
  // 双 rAF 稳定窗口
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));
}

test.describe("吸收批 REVIEW 哨兵 badge — 活体验收", () => {
  test("badge 渲染 + 对比度 + axe", async ({ page }, testInfo) => {
    await mockHealth(page);
    await mockWorkspace(page);
    await mockLessons(page);

    await gotoWorkspace(page);

    const reviewBadge = page.getByText("评级读不出·需人看").first();
    await expect(reviewBadge).toBeVisible();

    // 常规 rating 文本仍在(未被误伤)
    await expect(page.getByText("Buy").first()).toBeVisible();

    // ---- 对比度实测(getComputedStyle + canvas 归一颜色,非静态推理)----
    // Tailwind v4 输出 oklab() → 用 canvas 让浏览器把 CSS 色按真实 src-over 合成;
    // badge 背景 bg-warn/20 是半透明,必须叠在最近不透明祖先(surface)之上取有效背景。
    const measured = await reviewBadge.evaluate((el) => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const ctx = canvas.getContext("2d")!;
      // 把任意 CSS 颜色按 src-over 合成到不透明底色上 → 取真实 sRGB 字节
      function overOpaque(css: string, base: [number, number, number]): [number, number, number] {
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = `rgb(${base[0]},${base[1]},${base[2]})`;
        ctx.fillRect(0, 0, 1, 1);
        ctx.fillStyle = css;
        ctx.fillRect(0, 0, 1, 1);
        const d = ctx.getImageData(0, 0, 1, 1).data;
        return [d[0], d[1], d[2]];
      }
      function alphaOf(css: string): number {
        if (css.startsWith("rgba")) {
          const p = css.slice(5, -1).split(",");
          return p.length === 4 ? Number(p[3]) : 1;
        }
        const m = css.match(/\/\s*([\d.]+)\s*\)/); // oklab(... / 0.2)
        if (m) return Number(m[1]);
        return 1;
      }
      const cs = getComputedStyle(el);
      // 最近不透明祖先背景(卡片 bg-surface)
      let node: HTMLElement | null = el as HTMLElement;
      let opaqueCss = "rgb(4,7,11)";
      while (node) {
        const b = getComputedStyle(node).backgroundColor;
        if (alphaOf(b) === 1) { opaqueCss = b; break; }
        node = node.parentElement;
      }
      // 不透明底色本身(它无 alpha → 直接 over 白/黑都同值;用 over 白取)
      const opaqueRgb = overOpaque(opaqueCss, [255, 255, 255]);
      // 有效背景 = 自身 badge 半透明层叠在不透明祖先上
      const effBg = overOpaque(cs.backgroundColor, opaqueRgb);
      const fg = overOpaque(cs.color, opaqueRgb); // 文字色无 alpha,与底色无关
      return { fgCss: cs.color, fg, ownBgCss: cs.backgroundColor, opaqueCss, effBg, fontSize: cs.fontSize, fontWeight: cs.fontWeight };
    });

    const ratio = contrast(measured.fg, measured.effBg);
    console.log(`[REVIEW badge] color=${measured.fgCss} ownBg=${measured.ownBgCss} effBg=rgb(${measured.effBg.join(",")}) ratio=${ratio.toFixed(2)}:1 font=${measured.fontSize}/${measured.fontWeight}`);
    testInfo.attach("review-badge-contrast", {
      body: JSON.stringify({ ...measured, ratio: Number(ratio.toFixed(2)) }, null, 2),
      contentType: "application/json",
    });
    expect(ratio, `对比度 ${ratio.toFixed(2)}:1 低于 AA 4.5:1`).toBeGreaterThanOrEqual(4.5);

    // ---- badge 是 warn 色系(不是 ink 灰):warn #e0af3a → rgb(224,175,58) ----
    expect(Math.abs(measured.fg[0] - 224)).toBeLessThan(30);
    expect(Math.abs(measured.fg[1] - 175)).toBeLessThan(30);

    // ---- axe 全页扫描(直接注入 axe-core 源码,规避 CSP addScriptTag 限制)----
    const axe = await page.evaluate(async (src: string) => {
      // eslint-disable-next-line no-new-func
      new Function(src)();
      const w = window as unknown as { axe: Axe };
      return await w.axe.run(document, { resultTypes: ["violations"] });
    }, axeSource);
    const blockers = axe.violations.filter((v) => v.impact === "critical" || v.impact === "serious");
    testInfo.attach("axe-violations", {
      body: JSON.stringify(axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })), null, 2),
      contentType: "application/json",
    });
    console.log(`[axe] violations: ${axe.violations.map((v) => `${v.id}(${v.impact},${v.nodes.length})`).join(", ") || "none"}`);
    expect(blockers, `axe serious/critical: ${JSON.stringify(blockers.map((v) => v.id))}`).toEqual([]);

    // ---- 截图 ----
    await page.screenshot({ path: testInfo.outputPath("agent-trading-workspace.png"), fullPage: true });
  });
});
