// 设置页 P1(信息架构:组名导航 / 搜索 / 标题层级 / 折叠)验收探针
//
// 六道门逐条实测,全部对着真浏览器量几何与 DOM 文本:
//   1. 原始 JSON key 标题数 = 0      —— 扫 body 里的 snake_case 词
//   2. h1/h2 数 ≥ 6                  —— 1 个 h1 + 每个面板一个 h2
//   3. 搜「代理」→ 卡片数下降且含网络面板
//   4. 点第 13 个 chip → 目标 top 落在视口内且不被 sticky 导航压住
//   5. 高级配置默认折叠,展开后只读组可见
//   6. en 模式下分组名零 CJK 残留
//
// 为什么要把 /api 整体转发:这个树上的 next dev(3001)把 /api 重写到已死的 8088。
// 只转 settings 不够 —— 启动门读 /api/v1/health,转了 settings 页面会永远卡在
// 「Connecting to runtime」。所以整条 /api/** 转到真后端,其余(含 next 资源)走 3001。
//
// 用法:PROBE_API=http://127.0.0.1:8090 node scripts/probe-settings-ia.mjs
// 只读:不点任何写操作,不发 POST/PUT。
import { chromium } from "playwright";

const OUT = process.env.PROBE_SHOTS ?? "<local-path-redacted>";
const BASE = process.env.PROBE_BASE ?? "http://localhost:3001";
const API = process.env.PROBE_API ?? "http://127.0.0.1:8090";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

await ctx.route("**/api/**", async (route) => {
  const url = new URL(route.request().url());
  const req = route.request();
  const method = req.method();
  try {
    const res = await fetch(`${API}${url.pathname}${url.search}`, {
      method,
      headers: { "content-type": req.headers()["content-type"] ?? "application/json" },
      body: ["GET", "HEAD"].includes(method) ? undefined : req.postData() ?? undefined,
    });
    await route.fulfill({
      status: res.status,
      contentType: res.headers.get("content-type") ?? "application/json",
      body: await res.text(),
    });
  } catch (e) {
    await route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ error: String(e) }) });
  }
});

const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

/** 等到分区真的挂载(数据到达),而不是固定睡一段时间。 */
const settle = async () => {
  await page.waitForSelector("[data-settings-section]", { timeout: 60000 });
  await page.waitForFunction(
    () => document.querySelectorAll('[id^="set-group-"]').length > 0,
    { timeout: 60000 },
  );
  await page.waitForTimeout(400);
};

await page.goto(`${BASE}/system/set`, { waitUntil: "domcontentloaded", timeout: 120000 });
await settle();

const read = async () =>
  page.evaluate(() => {
    const txt = (el) => (el?.textContent ?? "").trim();
    const sections = [...document.querySelectorAll("[data-settings-section]")];
    return {
      titleTexts: [
        ...document.querySelectorAll('[class*="set-group-title"]'),
        ...document.querySelectorAll("h1,h2,h3,summary"),
      ]
        .map(txt)
        .filter(Boolean),
      h1: [...document.querySelectorAll("h1")].map(txt),
      h2: [...document.querySelectorAll("h2")].map(txt),
      // 门 1:正文里还剩下的 snake_case 词(裸 JSON key 的指纹)
      bodyKeys: [
        ...new Set((document.body.textContent || "").match(/\b[a-z]+(?:_[a-z0-9]+)+\b/g) || []),
      ],
      sectionIds: sections.map((s) => s.id),
      chips: [...document.querySelectorAll('[role="navigation"] button')].map(txt),
      detailsOpen: document.querySelector("details")?.open ?? null,
      groupCount: document.querySelectorAll('[id^="set-group-"]').length,
      groupIds: [...document.querySelectorAll('[id^="set-group-"]')].map((s) => s.id),
      navH: Math.round(document.querySelector('[role="navigation"]')?.getBoundingClientRect().height ?? -1),
      scrollH: document.querySelector(".v17-page-grid")?.scrollHeight ?? null,
    };
  });

const zh = await read();
await page.screenshot({ path: `${OUT}/zh-collapsed.png` });

// 门 5:展开高级配置
await page.click("details summary");
await page.waitForTimeout(600);
const expanded = await page.evaluate(() => ({
  open: document.querySelector("details")?.open ?? null,
  groups: [...document.querySelectorAll('[id^="set-group-"]')].map((s) => ({
    id: s.id,
    h2: (s.querySelector("h2")?.textContent ?? "").trim(),
  })),
}));
await page.screenshot({ path: `${OUT}/zh-expanded.png` });

// 门 4:点第 13 个 chip,量真实几何
const jump = await page.evaluate(async () => {
  const chips = [...document.querySelectorAll('[role="navigation"] button')];
  const chip = chips[12];
  const label = (chip?.textContent ?? "").trim();
  chip?.click();
  await new Promise((r) => setTimeout(r, 900));
  const grid = document.querySelector(".v17-page-grid");
  const nav = document.querySelector('[role="navigation"]');
  const navH = Math.round(nav?.getBoundingClientRect().height ?? -1);
  const actives = chips
    .filter((c) => c.getAttribute("aria-current") === "true")
    .map((c) => c.textContent.trim());
  // 第 13 个 chip 是第 7 个只读组(sentinel)
  const targetId = "set-group-sentinel";
  const rows = [...document.querySelectorAll("[data-settings-section]")].map((el) => {
    const r = el.getBoundingClientRect();
    return { id: el.id, top: Math.round(r.top), bottom: Math.round(r.bottom) };
  });
  const target = rows.find((r) => r.id === targetId) ?? null;
  return {
    chipLabel: label,
    activeChips: actives,
    navH,
    targetId,
    targetRow: target,
    // 贴顶的分区若 top < navH,就是被 sticky 导航条压住了
    targetClearOfNav: target ? target.top >= navH : null,
    inViewport: rows.filter((r) => r.bottom > 0 && r.top < window.innerHeight),
    gridScrollTop: grid?.scrollTop ?? null,
    gridMaxScroll: grid ? grid.scrollHeight - grid.clientHeight : null,
  };
});

// 门 3:搜索「代理」
await page.fill('input[type="search"]', "代理");
await page.waitForTimeout(700);
const search = await page.evaluate(() => ({
  sections: [...document.querySelectorAll("[data-settings-section]")].map((s) => s.id),
  hasNetwork: !!document.getElementById("set-network"),
  networkText: (document.getElementById("set-network")?.textContent ?? "").slice(0, 120),
}));
await page.screenshot({ path: `${OUT}/zh-search-proxy.png` });

// 门 6:en 模式
await page.fill('input[type="search"]', "");
await page.waitForTimeout(400);
await page.evaluate(() => localStorage.setItem("selenespace-locale", "en"));
await page.reload({ waitUntil: "domcontentloaded" });
await settle();
await page.click("details summary").catch(() => {});
await page.waitForTimeout(600);
const en = await page.evaluate(() => {
  const txt = (el) => (el?.textContent ?? "").trim();
  const cjk = /[\u4e00-\u9fff]/;
  return {
    h1: [...document.querySelectorAll("h1")].map(txt),
    h2: [...document.querySelectorAll("h2")].map(txt),
    chips: [...document.querySelectorAll('[role="navigation"] button')].map(txt),
    groupH2: [...document.querySelectorAll('[id^="set-group-"] h2')].map(txt),
    // 任何标题/summary 里残留中文 = 漏译
    cjkTitles: [...document.querySelectorAll('[class*="set-group-title"], h1, h2, summary')]
      .map(txt)
      .filter((t) => cjk.test(t)),
    cjkChips: [...document.querySelectorAll('[role="navigation"] button')]
      .map(txt)
      .filter((t) => cjk.test(t)),
  };
});
await page.screenshot({ path: `${OUT}/en-expanded.png` });

console.log(JSON.stringify({ zh, expanded, jump, search, en, errors }, null, 1));
await browser.close();
