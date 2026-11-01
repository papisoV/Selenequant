import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// @axe-core/playwright 未安装 → 直接注入 axe-core 源码(同 review-badge-live.spec.ts)
const axeSource = readFileSync(resolve("node_modules/axe-core/axe.min.js"), "utf8");
type Axe = {
  run: (
    ctx: unknown,
    opts?: unknown,
  ) => Promise<{ violations: Array<{ id: string; impact?: string | null; nodes: unknown[] }> }>;
};

// 窄屏 shell 可用性 + 页面地标 — P1+P2 修复验收
// 计划:internal-plan §4 AC1-7
//
//   根因:.app-shell 固定 rail 56+200=256px + overflow:hidden,≤420px 主列仅剩 104px
//   修法:@media(max-width:768px) 用 `:root{--mid-w:0 !important}` 折 sidebar 列(必须
//        !important 才胜 useResize 写的 <html> 内联 var),sidebar 改可召唤覆盖层抽屉。
//
// 断点选择必须含 **769px**(断点上方紧邻处)——媒体查询笔误最易漏区。
// 数据全 mock,不点 destructive 控件,不固定 sleep。

const PORT = process.env.E2E_PORT ?? "3011";
const BASE = `http://localhost:${PORT}`;

// 被审页(与 review-badge-live 同页);选它是为了复用 mock,且它承载 P2 地标修复
const PAGE_PATH = "/system/agent-trading-workspace";

const BREAKPOINTS = [360, 420, 768, 769, 1280] as const;

async function mockApis(page: Page) {
  await page.route("**/api/v1/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) }),
  );
  await page.route("**/api/v1/agent/trading/workspace*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        positions: [{ symbol: "BTCUSDT", side: "long", size: 1, entry_price: 60000, mark_price: 61000, unrealized_pnl: 1000 }],
        pending_orders: [],
        active_strategies: [{ id: "s1", name: "bb_v3", symbol: "BTCUSDT", status: "running" }],
        account_summary: { balance: 100000, equity: 101000, unrealized_pnl: 1000, available: 90000 },
      }),
    }),
  );
  await page.route("**/api/v1/agent/decision-lessons*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }),
  );
}

/** 等 app-shell + 主列挂载 + 双 rAF 稳定(禁固定 sleep) */
async function gotoShell(page: Page) {
  await page.goto(`${BASE}${PAGE_PATH}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-shell", { timeout: 20_000 });
  await page.waitForSelector(".main", { timeout: 20_000 });
  await page.evaluate(
    () => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))),
  );
}

async function setWidth(page: Page, w: number, h = 900) {
  await page.setViewportSize({ width: w, height: h });
  await page.evaluate(
    () => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))),
  );
}

interface ShellMetrics {
  docScrollW: number;
  docClientW: number;
  sceneLayoutW: number;
  mainLeft: number;
  feedBarLeft: number;
  sidebarDisplay: string;
  vheroW: number;
  sidebarW: number;
  mainW: number;
}

async function measure(page: Page): Promise<ShellMetrics> {
  return page.evaluate(() => {
    const rect = (sel: string) => {
      const el = document.querySelector(sel) as HTMLElement | null;
      return el ? el.getBoundingClientRect() : null;
    };
    const sidebar = document.querySelector(".app-shell > .sidebar") as HTMLElement | null;
    const de = document.documentElement;
    return {
      docScrollW: de.scrollWidth,
      docClientW: de.clientWidth,
      sceneLayoutW: rect(".scene-layout")?.width ?? -1,
      mainLeft: rect(".main")?.left ?? -1,
      feedBarLeft: rect(".feed-bar")?.left ?? -1,
      sidebarDisplay: sidebar ? getComputedStyle(sidebar).display : "none",
      vheroW: rect(".vhero")?.width ?? -1,
      sidebarW: rect(".sidebar")?.width ?? -1,
      mainW: rect(".main")?.width ?? -1,
    };
  });
}

test.describe("窄屏 shell 可用性(P1)+ 地标(P2)", () => {
  // AC1 — 无横向溢出(360/420/768)+ 主列占满(rail 已折)
  test("AC1 窄屏无横向溢出且主列占满", async ({ page }) => {
    await mockApis(page);
    await gotoShell(page);
    for (const w of [360, 420, 768] as const) {
      await setWidth(page, w);
      const m = await measure(page);
      // 容差 +6px:.app-shell 用 width:100vw 含滚动条宽(本站 5px),纵滚时预存 ~5px 溢出
      expect(
        m.docScrollW,
        `视口 ${w}px:scrollWidth ${m.docScrollW} > clientWidth ${m.docClientW} + 6`,
      ).toBeLessThanOrEqual(m.docClientW + 6);
      // 主列 ≥ viewport - 60px → 证明 200px 的 sidebar rail 已折
      expect(
        m.sceneLayoutW,
        `视口 ${w}px:主列宽 ${m.sceneLayoutW} 未占满(>${w - 60} 期望),rail 未折`,
      ).toBeGreaterThan(w - 60);
    }
  });

  // AC2 — 抽屉可达:toggle 可点、打开可见、Esc 关闭
  test("AC2 覆盖层抽屉可达(点开/Esc 关)", async ({ page }) => {
    await mockApis(page);
    await gotoShell(page);
    await setWidth(page, 360);

    // 关闭态:sidebar 不在布局(display:none)
    expect((await measure(page)).sidebarDisplay).toBe("none");

    const toggle = page.locator(".sb-drawer-toggle");
    await expect(toggle).toBeVisible();
    await toggle.click();

    // 打开态:sidebar 以 fixed 覆盖层渲染且可见
    const sidebar = page.locator(".app-shell > .sidebar");
    await expect(sidebar).toBeVisible();
    expect(sidebar).toHaveClass(/drawer-open/);
    expect(await sidebar.evaluate((el) => getComputedStyle(el).position)).toBe("fixed");

    // 抽屉里的导航链接可用
    await expect(page.locator("#app-sidebar nav[aria-label='页面导航']")).toBeVisible();

    // Esc 关闭
    await page.keyboard.press("Escape");
    await expect(sidebar).not.toBeVisible();
    expect((await measure(page)).sidebarDisplay).toBe("none");
  });

  // AC3 — 宽屏零回归(1280 + 769 断点上方紧邻处),与"未改动基线"逐像素一致
  test("AC3 宽屏(1280/769)shell 尺寸零回归", async ({ page }) => {
    await mockApis(page);
    await gotoShell(page);
    for (const w of [769, 1280] as const) {
      await setWidth(page, w);
      const m = await measure(page);
      // 宽屏:sidebar 常驻(display:flex),三列不变
      expect(m.sidebarDisplay, `视口 ${w}px:宽屏 sidebar 应常驻`).toBe("flex");
      expect(m.vheroW, `视口 ${w}px:vhero 宽应为 56`).toBeGreaterThan(55);
      expect(m.vheroW, `视口 ${w}px:vhero 宽应为 56`).toBeLessThan(57);
      expect(m.sidebarW, `视口 ${w}px:sidebar 宽应为 200(默认 mid-w)`).toBeGreaterThan(199);
      expect(m.sidebarW, `视口 ${w}px:sidebar 宽应为 200`).toBeLessThan(201);
      // 主列 = 视口 - vhero - sidebar(三列都在)
      const expected = w - 56 - 200;
      expect(Math.abs(m.mainW - expected), `视口 ${w}px:主列宽 ${m.mainW} ≠ ${expected}`).toBeLessThanOrEqual(1);
      // toggle 在宽屏必须隐藏(CSS display:none)
      await expect(page.locator(".sb-drawer-toggle")).toBeHidden();
    }
  });

  // AC6 — 反向验证守卫:先拖拽 resizer 写内联 var,再缩窄屏,rail 仍须折叠
  // (若折叠退化成无 !important 的 `:root{--mid-w:0}`,内联 200px 会击败它 → 本测失败)
  test("AC6 拖拽写内联 var 后缩窄屏,rail 仍折叠", async ({ page }) => {
    await mockApis(page);
    await gotoShell(page);
    await setWidth(page, 1280);

    // 拖 mid-w 的 resizer 边界(sidebar|main),产生内联 --mid-w
    const handle = page.locator(".layout-resizer").nth(1);
    const box = await handle.boundingBox();
    if (box) {
      await page.mouse.move(box.x + box.width / 2, box.y + 100);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 40, box.y + 100, { steps: 5 });
      await page.mouse.up();
    }
    // 断言内联 var 确实写上了(证明本测在测内联击败路径)
    const inlineVar = await page.evaluate(() =>
      document.documentElement.style.getPropertyValue("--mid-w"),
    );
    expect(inlineVar, "拖拽未写内联 --mid-w,反向验证失效").not.toBe("");

    // 缩到窄屏:即使有内联 var,rail 仍须折叠(靠 !important)
    await setWidth(page, 360);
    const m = await measure(page);
    expect(m.sidebarDisplay, "内联 --mid-w 击败了折叠 → !important 缺失").toBe("none");
    expect(m.sceneLayoutW, `窄屏主列 ${m.sceneLayoutW} 未占满`).toBeGreaterThan(360 - 60);
    expect(m.docScrollW).toBeLessThanOrEqual(m.docClientW + 6);
  });

  // AC7 — 底部条对齐(结构性守卫:任何 fixed 消费 shell 变量的元素须与主列左边界对齐)
  test("AC7 窄屏 feed-bar 左边界对齐主列", async ({ page }) => {
    await mockApis(page);
    await gotoShell(page);
    await setWidth(page, 360);
    const m = await measure(page);
    expect(
      Math.abs(m.feedBarLeft - m.mainLeft),
      `feed-bar.left ${m.feedBarLeft} 与 main.left ${m.mainLeft} 差 >1px(--mid-w 折叠漏修)`,
    ).toBeLessThanOrEqual(1);
  });

  // AC4+AC5 — h1 归位 + region 归 0(axe 实测)
  test("AC4/AC5 axe:h1 归位 + region 归 0", async ({ page }, testInfo) => {
    await mockApis(page);
    await gotoShell(page);
    await setWidth(page, 1280);

    const axe = await page.evaluate(async (src: string) => {
      // eslint-disable-next-line no-new-func
      new Function(src)();
      const w = window as unknown as { axe: Axe };
      return await w.axe.run(document, { resultTypes: ["violations"] });
    }, axeSource);

    const ids = axe.violations.map((v) => v.id);
    testInfo.attach("axe-violations", {
      body: JSON.stringify(
        axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
        null,
        2,
      ),
      contentType: "application/json",
    });
    console.log(`[axe] ${ids.join(", ") || "none"}`);

    // AC4:h1 存在且可见文本匹配页面标题
    const h1s = page.locator("h1");
    await expect(h1s).toHaveCount(1);
    await expect(h1s).toHaveText("交易工作台");
    expect(ids, "page-has-heading-one 未归 0").not.toContain("page-has-heading-one");

    // AC5:region 违例归 0
    expect(ids, "region 违例未归 0").not.toContain("region");

    // 截图存证
    await page.screenshot({ path: testInfo.outputPath("narrow-shell-1280.png"), fullPage: true });
  });
});
