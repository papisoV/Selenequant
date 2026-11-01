// 概念成分弹窗 — 渲染验收探针(plan §6.4)
// 用法: node scripts/probe-concept-members-dialog.mjs <outJson> [port]
// 前置: 另一个终端已起 `npx next dev -p <port>` 且后端在跑(成功态走路由拦截,不依赖后端)。
// 产出: JSON(各场景指标) + .log(逐条 PASS/FAIL) + 每个场景一张 PNG。
import { chromium } from "playwright";
import { appendFileSync, writeFileSync } from "node:fs";
import { readFileSync } from "node:fs";

const OUT = process.argv[2] ?? "<local-path-redacted>";
const PORT = process.argv[3] ?? "3111";
const BASE = `http://127.0.0.1:${PORT}`;
const axeSource = readFileSync(
  new URL("../node_modules/axe-core/axe.min.js", import.meta.url),
  "utf-8",
);

const log = (m) => {
  appendFileSync(OUT + ".log", m + "\n");
  console.log(m);
};

const DEGRADED = (reason) => ({
  meta: {},
  data: {
    status: "degraded",
    reason,
    concept: "融资融券",
    limit: 1000,
    membership_note: "概念成分为当前快照",
  },
});

// RPS 卡片是弹窗的入口。真实后端此刻对 RPS 回「暂无数据」,入口就不会渲染,
// 于是所有弹窗断言都无从谈起。这里只替换**入口数据**,被测对象仍是真弹窗。
const RPS = {
  meta: {},
  data: {
    status: "ok",
    reason: null,
    lookback: 120,
    concept_count: 1179,
    window_days: 120,
    as_of: "20261023",
    concepts: [
      { concept: "融资融券", rps: 98.7, ret: 12.4, member_count: 1200, stability: "solid", is_strong: true },
      { concept: "印制电路板", rps: 99.8, ret: 16.62, member_count: 46, stability: "solid", is_strong: true },
      { concept: "超硬材料", rps: 100.0, ret: 36.62, member_count: 4, stability: "thin", is_strong: false },
    ],
  },
};

const OK = (() => {
  const rows = Array.from({ length: 60 }, (_, i) => ({
    symbol: `6000${String(i).padStart(2, "0")}.SH`,
    name: `样本股${i + 1}`,
    last: 10 + i * 0.13,
    change_pct: Number((((i % 13) - 6) * 0.87).toFixed(2)),
    amount: 1.2e8 + i * 3.4e6,
    priced: true,
  }));
  return {
    meta: {},
    data: {
      status: "ok",
      reason: null,
      concept: "融资融券",
      limit: 1000,
      total: 1200,
      returned: rows.length,
      truncated: true,
      rows,
      quote_source: "snapshot",
      quote_reason: null,
      as_of: "20261023",
      priced_count: rows.length,
      membership_note: "概念成分为当前快照",
    },
  };
})();

async function withRoutes(page, mode) {
  await page.route("**/api/v1/ashare/concept-rps**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RPS),
    }),
  );
  await page.route("**/api/v1/ashare/concept-members**", async (route) => {
    if (mode === "degraded-unavailable") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DEGRADED("concept_membership_unavailable")),
      });
    }
    if (mode === "degraded-empty") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DEGRADED("concept_membership_empty")),
      });
    }
    if (mode === "http-500") {
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "boom" }),
      });
    }
    if (mode === "ok") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(OK),
      });
    }
    if (mode === "slow") {
      await new Promise((r) => setTimeout(r, 60_000));
      return route.abort();
    }
    return route.continue();
  });
}

async function openDialog(page) {
  const trigger = page.locator('[title*="的成分股"]').first();
  await trigger.waitFor({ state: "visible", timeout: 30_000 });
  await trigger.click();
  await page.locator('[role="dialog"]').waitFor({ state: "visible", timeout: 15_000 });
}

async function snapshot(page) {
  return page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return { error: "no dialog" };
    const q = (sel) => dialog.querySelector(sel);
    const scroller = dialog.querySelector('[role="region"]');
    const stats = q('[data-testid="member-stats"]');
    // Radix 把 DialogDescription 的 id 挂在 Content 的 aria-describedby 上,
    // 按图索骥比猜选择器稳。
    const descId = (dialog.getAttribute("aria-describedby") ?? "")
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id))
      .find(Boolean);
    const subtitleEl = descId ?? dialog.querySelector("header p");
    const quoteEl = q('[data-testid="quote-source"]');
    const closeBtn = dialog.querySelector('button[class*="absolute"]');
    const alertEl = dialog.querySelector('[role="alert"]');
    const statusEl = dialog.querySelector('[role="status"]');
    const skeleton = q('[data-testid="member-skeleton"]');
    const radii = Array.from(dialog.querySelectorAll("input,select")).map(
      (e) => getComputedStyle(e).borderRadius,
    );
    const buttons = Array.from(dialog.querySelectorAll("button"));
    const fullText = dialog.textContent ?? "";
    return {
      fullText,
      subtitle: subtitleEl?.textContent ?? null,
      stats: stats?.textContent ?? null,
      quoteSource: quoteEl?.textContent?.trim() ?? null,
      scrollerRole: scroller?.getAttribute("role") ?? null,
      scrollerAriaLabel: scroller?.getAttribute("aria-label") ?? null,
      scrollerTabIndex: scroller?.getAttribute("tabindex") ?? null,
      hasScroller: !!scroller,
      retryButton: buttons.some((b) => /重试/.test(b.textContent ?? "")),
      buttonTexts: buttons.map((b) => (b.textContent ?? "").trim()).filter(Boolean),
      hasAlert: !!alertEl,
      alertText: alertEl?.textContent ?? null,
      hasStatus: !!statusEl,
      statusLabel: statusEl?.getAttribute("aria-label") ?? null,
      skeletonCount: skeleton ? skeleton.children.length : 0,
      radii: Array.from(new Set(radii)),
      hasPlaceholderLeak: /\{(total|returned|shown|concept|p0)\}/.test(fullText),
      machineCodeVisible: /concept_membership_/.test(dialog.innerText ?? ""),
      closeSrText: closeBtn?.querySelector(".sr-only")?.textContent ?? null,
      closeBox: closeBtn
        ? (() => {
            const r = closeBtn.getBoundingClientRect();
            return { w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10 };
          })()
        : null,
      docOverflowX:
        document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
}

const results = {};
const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const consoleWarnings = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleWarnings.push(m.text());
  });

  // 1. 降级(不可用)
  await withRoutes(page, "degraded-unavailable");
  await page.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  await openDialog(page);
  await page.waitForTimeout(1200);
  results.degradedUnavailable = await snapshot(page);
  await page.screenshot({ path: OUT.replace(/\.json$/, "-degraded.png") });

  let hits = 0;
  page.on("request", (r) => {
    if (r.url().includes("concept-members")) hits++;
  });
  const before = hits;
  const retry = page.locator('[role="dialog"] button', { hasText: "重试" });
  const retryCount = await retry.count();
  if (retryCount > 0) {
    await retry.first().focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(900);
  }
  results.degradedUnavailable.retryCount = retryCount;
  results.degradedUnavailable.retryRequests = hits - before;

  await page.addScriptTag({ content: axeSource });
  results.degradedUnavailable.axe = await page.evaluate(async () => {
    const dlg = document.querySelector('[role="dialog"]');
    const res = await window.axe.run(dlg, { resultTypes: ["violations"] });
    return res.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.length,
    }));
  });
  results.degradedUnavailable.consoleWarnings = consoleWarnings.slice();

  // 2. 降级(空)
  const page2 = await ctx.newPage();
  await withRoutes(page2, "degraded-empty");
  await page2.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  await openDialog(page2);
  await page2.waitForTimeout(800);
  results.degradedEmpty = await snapshot(page2);

  // 3. HTTP 500
  const page3 = await ctx.newPage();
  await withRoutes(page3, "http-500");
  await page3.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  await openDialog(page3);
  await page3.waitForTimeout(1000);
  results.httpError = await snapshot(page3);

  // 4. 加载态
  const page4 = await ctx.newPage();
  await withRoutes(page4, "slow");
  await page4.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  await openDialog(page4);
  await page4.waitForTimeout(700);
  results.loading = await snapshot(page4);

  // 5. 焦点归还
  const page5 = await ctx.newPage();
  await withRoutes(page5, "degraded-unavailable");
  await page5.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  await openDialog(page5);
  await page5.waitForTimeout(600);
  await page5.keyboard.press("Escape");
  await page5.waitForTimeout(300);
  const afterEsc = await page5.evaluate(() => {
    const el = document.activeElement;
    return {
      tag: el?.tagName ?? null,
      title: el?.getAttribute("title") ?? null,
      text: (el?.textContent ?? "").slice(0, 30),
    };
  });
  await openDialog(page5);
  await page5.waitForTimeout(600);
  // 真键盘走到关闭按钮:程序化 .focus() 不一定命中 :focus-visible 启发式。
  let reachedByTab = false;
  for (let i = 0; i < 25; i++) {
    await page5.keyboard.press("Tab");
    reachedByTab = await page5.evaluate(() => {
      const el = document.activeElement;
      return !!el && el.matches('[role="dialog"] button[class*="absolute"]');
    });
    if (reachedByTab) break;
  }
  const closeFocus = await page5.evaluate(() => {
    const btn = document.querySelector('[role="dialog"] button[class*="absolute"]');
    if (!btn) return null;
    const cs = getComputedStyle(btn);
    const r = btn.getBoundingClientRect();
    return {
      isFocused: document.activeElement === btn,
      outlineWidth: cs.outlineWidth,
      outlineStyle: cs.outlineStyle,
      outlineColor: cs.outlineColor,
      outlineOffset: cs.outlineOffset,
      box: { w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10 },
    };
  });
  closeFocus.reachedByTab = reachedByTab;
  await page5.locator('[role="dialog"] button[class*="absolute"]').first().click();
  await page5.waitForTimeout(300);
  const afterCloseBtn = await page5.evaluate(() => {
    const el = document.activeElement;
    return {
      tag: el?.tagName ?? null,
      title: el?.getAttribute("title") ?? null,
      text: (el?.textContent ?? "").slice(0, 30),
    };
  });
  results.focus = { afterEsc, afterCloseBtn, closeFocus };

  // 6. 真实数据(OK 路径)
  // 6. 真实数据(OK 路径)—— 真后端对该概念回 Not Found,故用托管数据走通 OK 分支,
  //    被测的仍是真组件/真虚拟列表/真渲染。
  const page6 = await ctx.newPage();
  await withRoutes(page6, "ok");
  await page6.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  let dialogOpened = true;
  try {
    await openDialog(page6);
  } catch {
    dialogOpened = false;
  }
  results.real = { dialogOpened };
  if (dialogOpened) {
    await page6.waitForTimeout(1800);
    results.real.snapshot = await snapshot(page6);
    results.real.list = await page6.evaluate(() => {
      const dlg = document.querySelector('[role="dialog"]');
      const sc = dlg?.querySelector('[role="region"]');
      if (!sc) return null;
      const rows = sc.querySelectorAll('[data-testid="member-row"]');
      return {
        nodes: rows.length,
        scrollHeight: sc.scrollHeight,
        clientHeight: sc.clientHeight,
      };
    });
    // 虚拟化证据:60 行数据下 DOM 里应只有视口附近的一小段。
    await page6.addScriptTag({ content: axeSource });
    results.real.axe = await page6.evaluate(async () => {
      const dlg = document.querySelector('[role="dialog"]');
      const res = await window.axe.run(dlg, { resultTypes: ["violations"] });
      return res.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
    });
  }

  // 7. 375px
  const ctxM = await browser.newContext({ viewport: { width: 375, height: 780 } });
  const page7 = await ctxM.newPage();
  await withRoutes(page7, "degraded-unavailable");
  await page7.goto(`${BASE}/watch/ashare-board`, { waitUntil: "domcontentloaded" });
  await openDialog(page7);
  await page7.waitForTimeout(800);
  results.mobile375 = await snapshot(page7);
  await page7.screenshot({ path: OUT.replace(/\.json$/, "-375.png") });
  await ctxM.close();
} finally {
  await browser.close();
  writeFileSync(OUT, JSON.stringify(results, null, 2), "utf-8");
  log("WROTE " + OUT);
}
