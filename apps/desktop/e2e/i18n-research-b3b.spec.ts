import { test, expect } from "@playwright/test";

/**
 * B3-b 真渲染证据 —— research 域(含 components/agent)双语切换门。
 *
 * B0 的门验的是**框架层**(侧栏 nav aria-label)翻转。本门补齐计划门 #4 对
 * B3-b 的要求:目标页的**正文文案**真的翻转 —— 断言的是 tr() 渲染产物,
 * 不是"词表里有键"(后者等于把词表抄一遍,空洞)。
 *
 * 双向断言(zh 有 / en 无)防"两种语言都渲染"这种假绿。
 *
 * 运行:cd apps/desktop && E2E_PORT=3011 npx playwright test e2e/i18n-research-b3b.spec.ts
 * (E2E_PORT 复用本副本已起的 dev server,避开 1.3G 生产构建 —— C: 盘余量吃紧)
 */

// 本 app 有行情轮询/SSE 流,network 永不 idle;dev 首击要编译 → 放宽超时。
test.setTimeout(120_000);

const CASES = [
  {
    path: "/research/desk",
    zh: "账本完整性",
    en: "Ledger integrity",
    why: "静态源串",
  },
  {
    path: "/research/missions",
    zh: "研究线 · Mission 一览",
    en: "Research Line · Mission Overview",
    why: "静态源串",
  },
  {
    // B3-b 补录:该串源自 `` `最近 ${BACKLOOK_DAYS} 天的变化` ``,原来是个裸模板串,
    // 既不进 codemod 也不进覆盖锁。选它作样本是为了钉住「模板串已转 tr({param})」
    // 这条修复 —— 只有真插值过,en 侧才会出现 "7"。
    path: "/research/desk",
    zh: "最近 7 天的变化",
    en: "Changes over the last 7 days",
    why: "模板串转 tr 后带实参",
  },
  {
    // ASHARE_STEPS 来自 CHAINS.research(纯常量,不依赖接口),故 "5 页" 必然渲染。
    path: "/research/rsch",
    zh: "5 页",
    en: "5 pages",
    why: "{n} 页 插值",
  },
  {
    // B3-b 补录:数据常量**属性访问**类 —— WORKBENCH_TABS[i].label 经 `tr(t.label)`
    // 渲染。这类串的源串在模块常量里,既不被 tr("…") 正则看见,也不被模板串锁看见;
    // 前一轮的覆盖锁(B3-b 新增那条)才兜住它。选 "治理"→"Governance" 是因为它
    // 在别处不出现,双向断言(zh 消失/en 出现)不会因他页同串而假绿。
    path: "/research/alpha_mine",
    zh: "治理",
    en: "Governance",
    why: "数据常量属性访问 tr(t.label)",
  },
] as const;

async function flipTo(page: import("@playwright/test").Page, locale: "en" | "zh-CN") {
  const btn = page.locator(
    locale === "en" ? 'button[title="Switch to English"]' : 'button[title="切换到中文"]',
  );
  if (await btn.count()) {
    await btn.first().click();
    await expect(page.locator("html")).toHaveAttribute("data-locale", locale);
  }
}

test.describe("i18n B3-b research 域真渲染翻转", () => {
  for (const c of CASES) {
    test(`${c.path} —「${c.zh}」zh↔en 真翻转`, async ({ page }) => {
      await page.goto(c.path, { waitUntil: "domcontentloaded" });
      await expect(page.locator("html")).toHaveAttribute("data-locale", "zh-CN");

      // 先证 zh 侧真的渲染出中文源串(等它出现,否则页还没挂载完)
      const zhNode = page.getByText(c.zh, { exact: false });
      await expect(zhNode.first()).toBeVisible({ timeout: 60_000 });

      // 翻到 en
      await flipTo(page, "en");
      const enNode = page.getByText(c.en, { exact: false });
      await expect(enNode.first()).toBeVisible({ timeout: 30_000 });

      // 双向:zh 串在 en 下必须消失(防"两种都渲染")
      await expect(page.getByText(c.zh, { exact: false })).toHaveCount(0);

      await page.screenshot({
        path: `test-results/i18n-b3b-en-${c.zh.replace(/[^\w一-鿿]/g, "_")}.png`,
        fullPage: false,
      });

      // 翻回 zh,确认可逆
      await flipTo(page, "zh-CN");
      await expect(page.getByText(c.zh, { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });
      await expect(page.getByText(c.en, { exact: false })).toHaveCount(0);
    });
  }
});
