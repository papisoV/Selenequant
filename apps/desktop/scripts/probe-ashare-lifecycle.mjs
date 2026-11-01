// A股看盘台生命周期验收探针(P1.1 轮询闸门 / P1.2 会话感知 / P1.3 新鲜度徽章)
//
// 三段:
//   A 真实态 —— 不拦截任何请求。收盘时段 → 所有可轮询端点 65s 窗口内 0 请求。
//   B 会话开 —— 只把 /session 的响应改成 polling:true,其余走真实后端。
//              用来量「盘中每档到底发了几次」:真实后端此刻是 CLOSED,
//              不这么做就只能测到闸门关闭的那一半。
//   C 过期态 —— 只把 /ladder 响应的 date 改成上一个交易日,其余原样。
//              徽章只在"数据日期 ≠ 今天"时渲染,正常收盘日是今天 ⇒ 不拦就看不到它。
//
// 只读:不点任何写操作,不发 POST。
import { chromium } from "playwright";

const BASE = process.env.PROBE_BASE ?? "http://localhost:3001";
const URL = `${BASE}/watch/ashare-board`;
const SETTLE_MS = 25_000;   // 首屏要等 overview(冷态实测 10s+)与慢档三块
const WINDOW_MS = 65_000;   // 与计划里「65 秒窗口内 ladder 请求数」同口径
const SHOT_DIR = "<local-path-redacted>";

const POLLABLE = [
  "/api/v1/ashare/limit-up-pool",
  "/api/v1/ashare/ladder",
  "/api/v1/ashare/phase",
  "/api/v1/ashare/boards",
  "/api/v1/ashare/overview",
  "/api/v1/ashare/deviation",
  "/api/v1/ashare/rotation",
  "/api/v1/ashare/mainline",
  "/api/v1/ashare/concept-rps",
];

const dataPath = (u) => {
  const m = /\/api\/v1\/ashare\/[a-z0-9-]+/.exec(u);
  return m ? m[0] : null;
};

async function run(browser, { name, shot, interceptSession, staleLadderDate }) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  const hits = [];
  page.on("request", (r) => {
    if (!/\/api\/v1\/ashare\//.test(r.url())) return;
    const p = dataPath(r.url());
    if (p) hits.push({ t: Date.now(), path: p });
  });

  if (interceptSession) {
    await page.route("**/api/v1/ashare/session*", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.data = { ...body.data, polling: true, phase: "OPEN" };
      await route.fulfill({ response: res, json: body });
    });
  }
  if (staleLadderDate) {
    await page.route("**/api/v1/ashare/ladder*", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      if (body.data) body.data = { ...body.data, date: staleLadderDate };
      await route.fulfill({ response: res, json: body });
    });
  }

  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await page.waitForTimeout(SETTLE_MS);

  const headers = await page.evaluate(() => {
    // 不能写 querySelector("span.text-[10px]") —— Tailwind 的任意值类名含方括号,
    // 在 CSS 里是属性选择器语法。按 classList 精确匹配。
    const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const has = (el, cls) => !!(el && el.classList && el.classList.contains(cls));
    const out = [];
    for (const card of document.querySelectorAll(".rp-card")) {
      if (!visible(card)) continue;
      const row = card.firstElementChild;
      if (!row) continue;
      const spans = [...row.querySelectorAll("span")];
      const titleEl = spans.find((s) => has(s, "text-[10px]"));
      const rightEl = spans.find((s) => has(s, "text-[9px]"));
      if (!titleEl) continue;
      out.push({
        title: (titleEl.textContent || "").trim(),
        right: rightEl ? (rightEl.innerText || "").replace(/\s+/g, " ").trim() : "",
      });
    }
    return out;
  });

  const windowStart = Date.now();
  await page.waitForTimeout(WINDOW_MS);
  const during = hits.filter((h) => h.t >= windowStart);

  const perPath = {};
  for (const p of POLLABLE) perPath[p] = 0;
  for (const h of during) perPath[h.path] = (perPath[h.path] ?? 0) + 1;

  const shotPath = `${SHOT_DIR}/${shot}.png`;
  await page.screenshot({ path: shotPath, fullPage: true });

  const body = await page.evaluate(() => ({
    textLen: document.body.innerText.length,
    broken: document.body.innerText.includes("Something went wrong") ||
            document.body.innerText.includes("Application error"),
  }));

  await page.close();
  return {
    name,
    shot: shotPath,
    initialApiCalls: hits.length,
    windowSec: Math.round((Date.now() - windowStart) / 1000),
    perPath,
    cardHeaders: headers,
    body,
  };
}

const browser = await chromium.launch();
const results = [];
try {
  results.push(await run(browser, { name: "A_closed_real", shot: "ashare-lifecycle-closed" }));
  results.push(await run(browser, { name: "B_open_mocked_session", shot: "ashare-lifecycle-open", interceptSession: true }));
  results.push(
    await run(browser, {
      name: "C_stale_ladder",
      shot: "ashare-lifecycle-stale",
      staleLadderDate: "20261020",
    })
  );
} finally {
  await browser.close();
}

// 控制台是 GBK:中文直接 print 会 UnicodeEncodeError,写 UTF-8 文件由外部读。
const fs = await import("node:fs");
fs.writeFileSync(`${SHOT_DIR}/ashare-lifecycle.json`, JSON.stringify(results, null, 2), "utf8");
console.log("wrote <local-path-redacted>");
