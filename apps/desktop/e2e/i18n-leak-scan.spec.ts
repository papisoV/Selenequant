import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * B3-b 数据层泄漏普查(非门,是量具)。
 *
 * 目的:把「数据常量里的中文」这一类泄漏从"静态猜 AST 形状"换成
 * **看真渲染产物** —— 切到 en 后遍历 research 各路由,抓取所有仍可见的
 * 中文字符。这样得到的是**用户真会看到的漏网中文**,不管它来自裸字面量、
 * 常量表、还是 MAP[key] 查表。
 *
 * 每路由一个 test(worker 并行);结果各写一个 JSON,跑完用
 * aggregate-leaks.mjs 汇总。
 *
 * 运行:E2E_PORT=3011 npx playwright test e2e/i18n-leak-scan.spec.ts --workers=4
 */

test.setTimeout(90_000);

const OUT_DIR = "test-results/leak-scan";

const ROUTES = [
  "/research/desk", "/research/today", "/research/missions", "/research/questions",
  "/research/experiments", "/research/experiment-evidence", "/research/episodes",
  "/research/mem", "/research/rsch", "/research/graph", "/research/factors",
  "/research/alpha_mine", "/research/alpha-formulas", "/research/alpha-saved-strategies",
  "/research/alpha-vocab", "/research/scanner", "/research/strategies",
  "/research/strategy-cockpit", "/research/strat", "/research/tuning",
  "/research/optimizer", "/research/research-tuning-results", "/research/research-tuning-tasks",
  "/research/pa", "/research/serenity", "/research/serenity-factors",
  "/research/serenity-scores", "/research/qevolve", "/research/indicator_editor",
  "/research/ashare-backtest", "/research/ashare-factor-report", "/research/ashare-fundamentals",
  "/research/ashare-portfolio", "/research/ashare-style",
];

// 合法保留中文:LLM prompt / 协议比较值 / 语言切换按钮自身标签。
// 「中文」是切换按钮的标签(en 模式下显示"中文"表示点它切到中文),按定义
// 必须留中文 —— 它不是漏译,是目标语言名。34 条路由全报它是预期。
const ALLOW = ["[最近诊断上下文]", "被 revision v", "中文"];

const slug = (r: string) => r.replace(/^\//, "").replace(/\//g, "_");

for (const route of ROUTES) {
  test(`leak-scan ${route}`, async ({ page }) => {
    fs.mkdirSync(OUT_DIR, { recursive: true });
    const outFile = path.join(OUT_DIR, `${slug(route)}.json`);

    await page.goto(route, { waitUntil: "domcontentloaded" });

    // dev 首击要编译该路由(chunk 冷启动),固定 sleep 会假报"无切换按钮"。
    // 显式等切换按钮出现 —— 它挂在全局 shell 上,出现即代表页面已挂载。
    const btn = page.locator(
      'button[title="Switch to English"], button[title="切换到中文"]',
    );
    try {
      await btn.first().waitFor({ state: "visible", timeout: 60_000 });
    } catch {
      fs.writeFileSync(outFile, JSON.stringify({ route, error: "NO-SWITCH-BUTTON", found: [] }), "utf8");
      return;
    }
    // 若已是 en(理论上是干净 context,防御性处理)
    const toEn = page.locator('button[title="Switch to English"]');
    if (!(await toEn.count())) {
      fs.writeFileSync(outFile, JSON.stringify({ route, already: "en", found: [] }), "utf8");
      return;
    }
    await toEn.first().click();
    await expect(page.locator("html")).toHaveAttribute("data-locale", "en", { timeout: 15_000 });
    await page.waitForTimeout(1500);

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
      return out;
    }, ALLOW);

    fs.writeFileSync(outFile, JSON.stringify({ route, found: [...new Set(found)] }, null, 2), "utf8");
  });
}
