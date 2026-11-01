import { test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * 全站泄漏普查(量具,非门)。
 *
 * 切到 en,遍历路由,抓**真渲染产物里仍可见的中文**。
 * 运行:E2E_PORT=3011 npx playwright test e2e/i18n-leak-scan-all.spec.ts --workers=6
 *
 * 产物目录故意放在 scratch/tmp/ 而不是 test-results/ —— 后者是 Playwright 的默认
 * outputDir,跑任何别的 spec 都会把它清空,扫描结果会被别的调试顺手抹掉。
 *
 * 路由清单可用 SCAN_ROUTES 环境变量覆盖。注意:page-manifest.ts 有 absorbedBy
 * 条目与 188 个 slug,和真实 page.tsx 数对不上,清单应从文件系统枚举
 * (见 scratch/tmp/all-routes.json 的生成法)。
 */

test.setTimeout(180_000);

const OUT_DIR = "scratch/tmp/leak-scan-all";
const ROUTE_FILE = process.env.SCAN_ROUTES || "scratch/tmp/all-routes.json";
const ROUTES: string[] = JSON.parse(fs.readFileSync(ROUTE_FILE, "utf8"));

// 合法保留中文:语言切换按钮自身标签("中文"= 目标语言名)。
const ALLOW = ["中文"];

const slug = (r: string) => r.replace(/^\//, "").replace(/\//g, "_");

const SEL = 'button[title="Switch to English"], button[title="切换到中文"]';

for (const route of ROUTES) {
  test(`leak-scan ${route}`, async ({ page }) => {
    fs.mkdirSync(OUT_DIR, { recursive: true });
    const outFile = path.join(OUT_DIR, `${slug(route)}.json`);

    await page.goto(route, { waitUntil: "domcontentloaded" });

    // 等切语言按钮:轮询 DOM 里"可点"(非零尺寸、非隐藏)的实例。
    // 不能用 locator.waitFor({state:"visible"}) —— 慢挂载的路由(如 /watch/markets)
    // 会报 NO-SWITCH-BUTTON,而按钮其实在,是量具自身时序脆。
    const deadline = Date.now() + 90_000;
    let state = "absent";
    while (Date.now() < deadline) {
      state = await page.evaluate((sel) => {
        const els = [...document.querySelectorAll(sel)] as HTMLElement[];
        if (!els.length) return "absent";
        for (const b of els) {
          const r = b.getBoundingClientRect();
          const cs = getComputedStyle(b);
          const visible =
            r.width > 0 &&
            r.height > 0 &&
            cs.display !== "none" &&
            cs.visibility !== "hidden" &&
            Number(cs.opacity) > 0;
          if (visible) return "visible";
        }
        return "present-hidden";
      }, SEL);
      if (state === "visible") break;
      await page.waitForTimeout(500);
    }

    if (state !== "visible") {
      fs.writeFileSync(
        outFile,
        JSON.stringify({ route, error: `NO-SWITCH-BUTTON (${state})`, found: [] }, null, 2),
        "utf8"
      );
      return;
    }

    const toEn = page.locator('button[title="Switch to English"]');
    if (!(await toEn.count())) {
      fs.writeFileSync(outFile, JSON.stringify({ route, already: "en", found: [] }, null, 2), "utf8");
      return;
    }
    await toEn.first().click();
    try {
      await page.locator('html[data-locale="en"]').waitFor({ timeout: 15_000 });
    } catch {
      const loc = await page.evaluate(() => document.documentElement.getAttribute("data-locale"));
      fs.writeFileSync(
        outFile,
        JSON.stringify({ route, error: `SWITCH-FAILED (data-locale=${loc})`, found: [] }, null, 2),
        "utf8"
      );
      return;
    }
    await page.waitForTimeout(1800);

    const found: string[] = await page.evaluate((allow) => {
      const CJK = /[一-鿿]/;
      const out: string[] = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n: Node | null;
      while ((n = walker.nextNode())) {
        const t = (n.textContent || "").trim();
        if (!t || !CJK.test(t)) continue;
        const el = n.parentElement;
        if (!el) continue;
        const cs = getComputedStyle(el);
        if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") continue;
        if (el.closest("[aria-hidden='true']")) continue;
        if (allow.some((a) => t.includes(a))) continue;
        const m = t.match(/[一-鿿][^\n]{0,60}/);
        out.push((m ? m[0] : t).slice(0, 80));
      }
      // 属性面:文本节点游走覆盖不到 aria-label / title / placeholder / alt。
      // 这四类是纯展示文案(与逻辑值无关),漏扫会让"0 泄漏"变成假绿 ——
      // 实测静态扫描里 241 个未包的值大多就在这些属性上。
      for (const attr of ["aria-label", "title", "placeholder", "alt"]) {
        for (const el of document.querySelectorAll(`[${attr}]`)) {
          const v = (el.getAttribute(attr) || "").trim();
          if (!v || !CJK.test(v)) continue;
          if (allow.some((a) => v.includes(a))) continue;
          if (el.closest("[aria-hidden='true']")) continue;
          const cs = getComputedStyle(el as HTMLElement);
          if (cs.display === "none" || cs.visibility === "hidden") continue;
          const m = v.match(/[一-鿿][^\n]{0,60}/);
          out.push(`[${attr}] ${(m ? m[0] : v).slice(0, 80)}`);
        }
      }
      return out;
    }, ALLOW);

    fs.writeFileSync(outFile, JSON.stringify({ route, found: [...new Set(found)] }, null, 2), "utf8");
  });
}
