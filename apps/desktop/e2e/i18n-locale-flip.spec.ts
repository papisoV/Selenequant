import { test, expect } from "@playwright/test";

/**
 * B0 真渲染证据 —— 全站双语 i18n 切换门。
 *
 * 验的是"渲染点真翻转",不是"词表里有键":
 *   1. 首访默认 zh-CN(<html lang> + data-locale 双同步)
 *   2. 切换器点击 → 文案真的变(读 DOM aria-label,不读词表)
 *   3. <html lang> / data-locale / localStorage 三者同步
 *   4. 涨跌色随 locale 翻转(--color-pos 计算值,en 绿 / zh 红)
 *   5. 刷新后保持选择的语言(首帧引导脚本从 localStorage 复水)
 */

const TARGET = "/watch/kf";

// 本 app 有行情 WebSocket/轮询流,网络**永不 idle**;`networkidle` 必挂。
// 改用 domcontentloaded + 显式等锚点元素。dev 模式首击要编译,放宽超时。
test.setTimeout(120_000);

// 侧栏 nav 的 aria-label 是 tr("侧栏导航") 的渲染产物 → 断言翻转的最稳锚点
const NAV_ZH = "侧栏导航";
const NAV_EN = "Sidebar navigation";

function posColor(page: import("@playwright/test").Page) {
  return page.evaluate(() =>
    getComputedStyle(document.documentElement)
      .getPropertyValue("--color-pos")
      .trim(),
  );
}

test.describe("i18n 全站双语 B0", () => {
  test("首访默认 zh-CN:lang/data-locale/文案三者一致", async ({ page }) => {
    await page.goto(TARGET, { waitUntil: "domcontentloaded" });

    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
    await expect(page.locator("html")).toHaveAttribute("data-locale", "zh-CN");

    // 渲染点真的是中文(不是词表里"有键")
    await expect(page.locator(`[aria-label="${NAV_ZH}"]`).first()).toBeVisible();
    expect(await posColor(page)).toBe("#f3615c"); // 红涨(A 股惯例)

    await page.screenshot({ path: "test-results/i18n-b0-01-default-zh.png", fullPage: false });
  });

  test("切换器点击 → 文案/lang/色三者同步翻转", async ({ page }) => {
    await page.goto(TARGET, { waitUntil: "domcontentloaded" });
    await expect(page.locator("html")).toHaveAttribute("data-locale", "zh-CN");

    // 点击紧凑切换器(title 在 zh 下是 "Switch to English")
    await page.locator('button[title="Switch to English"]').first().click();

    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.locator("html")).toHaveAttribute("data-locale", "en");
    await expect(page.locator(`[aria-label="${NAV_EN}"]`).first()).toBeVisible();
    await expect(page.locator(`[aria-label="${NAV_ZH}"]`)).toHaveCount(0);
    expect(await posColor(page)).toBe("#61bc66"); // 绿涨

    // localStorage 落盘
    const stored = await page.evaluate(() =>
      localStorage.getItem("selenespace-locale"),
    );
    expect(stored).toBe("en");

    await page.screenshot({ path: "test-results/i18n-b0-02-toggled-en.png", fullPage: false });

    // 再点一次回到中文
    await page.locator('button[title="切换到中文"]').first().click();
    await expect(page.locator("html")).toHaveAttribute("data-locale", "zh-CN");
    await expect(page.locator(`[aria-label="${NAV_ZH}"]`).first()).toBeVisible();
    expect(await posColor(page)).toBe("#f3615c");
  });

  test("刷新后保持已选语言(首帧引导复水,无闪烁回退)", async ({ page }) => {
    await page.goto(TARGET, { waitUntil: "domcontentloaded" });
    await page.locator('button[title="Switch to English"]').first().click();
    await expect(page.locator("html")).toHaveAttribute("data-locale", "en");

    await page.reload({ waitUntil: "domcontentloaded" });

    // 关键:刷新后仍是 en —— 证明 layout.tsx 首帧脚本读了 localStorage,
    // 且服务端 DEFAULT_LOCALE(zh-CN) 没有把它覆盖回去
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.locator("html")).toHaveAttribute("data-locale", "en");
    await expect(page.locator(`[aria-label="${NAV_EN}"]`).first()).toBeVisible();
    expect(await posColor(page)).toBe("#61bc66");
  });
});
