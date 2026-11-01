import { test, expect } from "@playwright/test";

// 底部监控条筛选 + 默认静止实测(2026-08-14)
// 断言:默认静止(无 marquee)、筛选控件存在、金额档位生效、方向/币种筛选、播放切回 marquee
// 注1:必须 localhost 而非 127.0.0.1——Next dev 默认 allowedDevOrigins 只放行 localhost,127.0.0.1 请求 chunk 会 403
// 注2:只拦 localhost:3000 的代理 health 让 boot 门直通;127.0.0.1:8088 直连 health 不拦,
//      这样 feed 的 demo 兜底(8s 后探测后端不可达)能真正触发注入 makeSampleItems,筛选可确定性断言
const BASE = "http://localhost:3000";

test.describe("底部监控条筛选 + 默认静止实测", () => {
  test.beforeEach(async ({ page }) => {
    // boot 门直通:只 stub 走 Next 代理的 health(localhost:3000),不碰直连 8088 的 demo 探测
    await page.route("http://localhost:3000/api/v1/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }),
    );
  });

  test("默认静止、筛选控件可用、金额档位生效、播放切回滚动", async ({ page }) => {
    await page.goto(`${BASE}/research/strat`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".feed-bar", { timeout: 15000 });

    // 1. 等 demo 兜底条目(8s 后 makeSampleItems 注入;id 带 demo- 前缀),此时默认静止 → 静态行
    await page.waitForSelector(".feed-bar .feed-item", { timeout: 20000 });
    await expect(page.locator(".feed-bar .feed-static")).toHaveCount(1);
    expect(await page.locator(".feed-bar .feed-marquee").count()).toBe(0);

    // 2. 筛选控件存在
    expect(await page.locator(".feed-filters .feed-seg").count()).toBe(2);
    await expect(page.locator(".feed-symbol-input")).toHaveCount(1);

    // 3. 点 ≥$1M → 只留 whale 档(样本里 ≥$1M:ETH 爆多 2.5M / SOL 主力买 1.2M / BTC 爆多?无)
    await page.locator(".feed-seg-btn", { hasText: "≥$1M" }).click();
    await expect(page.locator(".feed-seg-btn.active", { hasText: "≥$1M" })).toHaveCount(1);
    const whaleTexts = await page.locator(".feed-bar .feed-item").allInnerTexts();
    expect(whaleTexts.length).toBeGreaterThan(0);
    for (const t of whaleTexts) {
      // ≥$1M 档下不应出现小档位(样本里 <$1M 有 XRP 540K / LINK 310K / AVAX 760K)
      expect(t).not.toMatch(/XRP|LINK|AVAX/);
    }

    // 4. 币种输入 ETH(先重置金额为 全部,否则 ≥$1M 会把 ETH 的 $980K 也筛掉) → 可见条目都含 ETH
    await page.locator(".feed-seg-btn", { hasText: /^全部$/ }).click();
    const input = page.locator(".feed-symbol-input");
    await input.fill("ETH");
    await page.waitForTimeout(300);
    const ethTexts = await page.locator(".feed-bar .feed-item").allInnerTexts();
    if (ethTexts.length > 0) {
      for (const t of ethTexts) expect(t.toUpperCase()).toContain("ETH");
    }

    // 5. 播放 → marquee 出现且无 duplicate-key 警告
    const warnings: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "warning" && msg.text().includes("same key")) warnings.push(msg.text());
    });
    await page.locator(".fba-btn[title='滚动播放']").click();
    await expect(page.locator(".feed-bar .feed-marquee")).toHaveCount(1);
    expect(warnings).toHaveLength(0);
  });

  test("设置面板:自定义金额生效 + 刷新记住", async ({ page }) => {
    await page.goto(`${BASE}/research/strat`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".feed-bar", { timeout: 15000 });
    await page.waitForSelector(".feed-bar .feed-item", { timeout: 20000 });

    // 打开设置面板
    await page.locator(".feed-settings-btn").click();
    await expect(page.locator(".feed-settings-panel")).toHaveCount(1);
    await expect(page.locator(".fsp-input")).toHaveCount(1);

    // 面板预设 ≥$1M → 小档全剔(XRP 540K / LINK 310K / AVAX 760K / ETH 980K)
    await page.locator(".feed-settings-panel .feed-seg-btn", { hasText: "≥$1M" }).click();
    await page.waitForTimeout(300);
    let texts = await page.locator(".feed-bar .feed-item").allInnerTexts();
    if (texts.length > 0) {
      for (const t of texts) expect(t).not.toMatch(/XRP|LINK|AVAX|ETH/);
    }

    // 自定义 ≥1.5M → 连 SOL 1.2M 也剔,只剩 BTC 2.5M + 异动
    await page.locator(".fsp-input").fill("1500000");
    await page.waitForTimeout(300);
    texts = await page.locator(".feed-bar .feed-item").allInnerTexts();
    if (texts.length > 0) {
      for (const t of texts) expect(t).not.toMatch(/SOL|XRP|LINK|AVAX|ETH/);
    }

    // 刷新 → 设置记住(输入值还在,过滤仍生效)
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(".feed-bar", { timeout: 15000 });
    await page.waitForSelector(".feed-bar .feed-item", { timeout: 20000 });
    await page.locator(".feed-settings-btn").click();
    await expect(page.locator(".feed-settings-panel")).toHaveCount(1);
    await expect(page.locator(".fsp-input")).toHaveValue("1500000");
    texts = await page.locator(".feed-bar .feed-item").allInnerTexts();
    if (texts.length > 0) {
      for (const t of texts) expect(t).not.toMatch(/SOL|XRP|LINK|AVAX|ETH/);
    }
  });
});
