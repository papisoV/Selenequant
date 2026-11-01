// 设置页 P2(标尺 / 响应式网格)before/after 探针
//
// 量四件事,全部来自真浏览器的计算样式与几何:
//   1. 信息性文本节点里 computed font-size ≤ 9.5px 的数量与文本(用来语义分级,
//      不是一刀切替换 —— 分组标题留 10px、装饰性 micro-label 留 9px)
//   2. 375px 宽:网格 scrollWidth vs clientWidth、解析后的 gridTemplateColumns
//   3. 1920px 宽:列数 + 滚动高度(改造不得变差)
//   4. 滚动容器里是否出现横向溢出元素(逐个子项量)
//
// 为什么整条 /api 转发:见 probe-settings-ia.mjs 顶部注释(启动门读 /api/v1/health)。
//
// 用法:PROBE_TAG=before node scripts/probe-settings-p2.mjs
// 只读:不点任何写操作,不发 POST/PUT。
import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const OUT = process.env.PROBE_SHOTS ?? "<local-path-redacted>";
const BASE = process.env.PROBE_BASE ?? "http://localhost:3001";
const API = process.env.PROBE_API ?? "http://127.0.0.1:8090";
const TAG = process.env.PROBE_TAG ?? "run";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();

/** 在一个视口下量一遍设置页。 */
async function measure(width, height, name) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2 });
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

  await page.goto(`${BASE}/system/set`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForSelector("[data-settings-section]", { timeout: 60000 });
  await page.waitForFunction(() => document.querySelectorAll('[id^="set-group-"]').length > 0, { timeout: 60000 });
  // 展开高级配置,让 10 个只读组也进测量面
  await page.click("details summary").catch(() => {});
  await page.waitForTimeout(600);

  const result = await page.evaluate(() => {
    const grid = document.querySelector(".v17-page-grid");

    // —— 1. 小字节点:逐元素取 computed font-size,只留含可见文本的叶子。
    //     分别在「整页」和「设置网格内」两个作用域统计 —— 侧栏/顶栏是别的页面的
    //     共用 chrome,不属于设置页的标尺问题,不能混进同一个数字。
    const collect = (root) => {
      const small = [];
      for (const el of root.querySelectorAll("*")) {
        if (!(el instanceof HTMLElement)) continue;
        if (el.offsetParent === null && getComputedStyle(el).position !== "fixed") continue;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (!(fs <= 9.5)) continue;
        const own = [...el.childNodes]
          .filter((n) => n.nodeType === Node.TEXT_NODE)
          .map((n) => n.textContent.trim())
          .join(" ")
          .trim();
        if (!own) continue;
        small.push({
          fs: Math.round(fs * 10) / 10,
          text: own.slice(0, 48),
          cls: (el.className || "").toString().slice(0, 90),
          tag: el.tagName.toLowerCase(),
          isGroupTitle: !!el.closest?.('[class*="set-group-title"]') ||
            /set-group-title/.test((el.className || "").toString()),
        });
      }
      return small;
    };
    const small = collect(document.body);
    const smallScoped = grid ? collect(grid) : [];

    // —— 2/3. 网格几何
    const cs = grid ? getComputedStyle(grid) : null;
    const gridBox = grid
      ? { clientW: grid.clientWidth, scrollW: grid.scrollWidth, scrollH: grid.scrollHeight, clientH: grid.clientHeight }
      : null;

    // —— 4. 横向溢出:任何子项的右缘超过网格可视右缘
    const overflowing = [];
    if (grid) {
      const gr = grid.getBoundingClientRect();
      for (const el of grid.querySelectorAll("[data-settings-section]")) {
        const r = el.getBoundingClientRect();
        if (r.right > gr.right + 1) {
          overflowing.push({ id: el.id, right: Math.round(r.right), gridRight: Math.round(gr.right) });
        }
      }
    }

    const byFs = small.reduce((acc, x) => {
      acc[x.fs] = (acc[x.fs] ?? 0) + 1;
      return acc;
    }, {});
    const byFsScoped = smallScoped.reduce((acc, x) => {
      acc[x.fs] = (acc[x.fs] ?? 0) + 1;
      return acc;
    }, {});

    return {
      gridTemplateColumns: cs?.gridTemplateColumns ?? null,
      gridBox,
      columnsParsed: cs?.gridTemplateColumns ? cs.gridTemplateColumns.split(" ").length : null,
      smallBySize: byFs,
      smallCount: small.length,
      smallSample: small.slice(0, 400),
      smallScopedBySize: byFsScoped,
      smallScopedCount: smallScoped.length,
      smallScopedSample: smallScoped.slice(0, 400),
      overflowing,
      scrollRoots: [...document.querySelectorAll("*")]
        .filter((el) => el instanceof HTMLElement && el.scrollWidth > el.clientWidth + 1)
        .slice(0, 10)
        .map((el) => ({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || "").toString().slice(0, 60),
          clientW: el.clientWidth,
          scrollW: el.scrollWidth,
        })),
    };
  });

  await page.screenshot({ path: `${OUT}/${TAG}-${name}.png`, fullPage: false });
  await ctx.close();
  return { ...result, errors };
}

const out = {
  tag: TAG,
  mobile375: await measure(375, 812, "375"),
  desktop1440: await measure(1440, 900, "1440"),
  desktop1920: await measure(1920, 1080, "1920"),
};

// 样本可能很大,全文落盘,控制台只打摘要
writeFileSync(`${OUT}/${TAG}.json`, JSON.stringify(out, null, 1));
console.log(JSON.stringify({
  tag: TAG,
  mobile375: {
    gridTemplateColumns: out.mobile375.gridTemplateColumns,
    gridBox: out.mobile375.gridBox,
    columnsParsed: out.mobile375.columnsParsed,
    smallCount: out.mobile375.smallCount,
    smallScopedCount: out.mobile375.smallScopedCount,
    overflowing: out.mobile375.overflowing.length,
    scrollRoots: out.mobile375.scrollRoots.length,
  },
  desktop1440: {
    gridTemplateColumns: out.desktop1440.gridTemplateColumns,
    gridBox: out.desktop1440.gridBox,
    smallScopedCount: out.desktop1440.smallScopedCount,
    smallScopedBySize: out.desktop1440.smallScopedBySize,
    overflowing: out.desktop1440.overflowing.length,
  },
  desktop1920: {
    gridTemplateColumns: out.desktop1920.gridTemplateColumns,
    gridBox: out.desktop1920.gridBox,
    columnsParsed: out.desktop1920.columnsParsed,
    smallScopedCount: out.desktop1920.smallScopedCount,
    smallScopedBySize: out.desktop1920.smallScopedBySize,
    overflowing: out.desktop1920.overflowing.length,
  },
  errors: [...out.mobile375.errors, ...out.desktop1440.errors, ...out.desktop1920.errors],
}, null, 1));

await browser.close();
