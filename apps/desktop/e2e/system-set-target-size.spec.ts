import { test, expect, type Page } from "@playwright/test";
import { mockHealth } from "./mock-health";

/**
 * /system/set 指针目标尺寸门 —— WCAG 2.2 SC 2.5.8 要求指针目标 >= 24x24 CSS px。
 *
 * 背景:globals.css 把 html 根字号钉在 11px(浏览器默认 16px),Tailwind 的 h-5/h-7
 * 实际落 13.75/19.25px,于是"看起来正常"的手写控件全都低于下限 —— 2026-10-15 修复前
 * /system/set 上有 23 个真阳性(输入框、保存按钮、ON/OFF 开关、行情滑块)。这条门防回退。
 *
 * 覆盖范围:只有 /system/set。其余 16 个 /system/* 页未纳入 —— 那里同样有 <24px 的
 * 手写控件(存量未治理),纳入即红。别把这个文件当成"全站几何已达标"的证据。
 *
 * 判定用命中区而非盒子:.risk-toggle-switch 盒子 32x18,但作者用 ::before{inset:-3px}
 * 把可点区域撑到 38x24 —— 盒子小不代表点不着。反之盒子够大也不一定点得着(被遮挡、
 * 或伪元素只在某一条边上撑开)。所以判定在扩到 24px 的采样点上打 elementFromPoint,
 * 而不是量 getBoundingClientRect。
 */

const MIN = 24;

// 指针目标 = 有交互语义的元素。刻意不含裸 [tabindex]:可聚焦的滚动区
// (role=log 的 .feed-static、role=region 之类)不是指针目标,SC 2.5.8 不适用。
const TARGET_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "select",
  "textarea",
  '[role="button"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="tab"]',
  '[role="menuitem"]',
].join(",");

// 门不依赖后端(与本目录 mock-health 同思路)。假数据刻意打开所有条件分支 ——
// api_key_configured / smart_mode / testnet —— 让"清除"按钮、智能模型三输入框、
// "测试连接"按钮都渲染出来。不打开的话门只测到默认态,分支里的控件漏测 = 假绿。
const SETTINGS_FIXTURE = {
  risk: {
    tiered_hard_stop_pct: 0.02,
    tiered_low_tier_threshold: 0.015,
    tiered_first_tier_threshold: 0.03,
    tiered_second_tier_threshold: 0.06,
    tiered_low_tier_drop_pct: 0.5,
    tiered_first_tier_drop_pct: 0.3,
    tiered_second_tier_drop_pct: 0.2,
    tiered_min_emit_interval_seconds: 30,
  },
  llm: {
    provider: "anthropic",
    base_url: "https://api.anthropic.com/v1",
    model: "claude-sonnet-5",
    api_key_configured: true,
    api_key_masked: "sk-ant-****1234",
    smart_mode: true,
    smart_models: { strategy: "claude-opus-5", analysis: "claude-sonnet-5", quick: "claude-haiku-4-5" },
  },
  exchange: {
    provider: "binance",
    market_type: "spot",
    testnet: false,
    api_key_configured: true,
    api_secret_configured: true,
    api_key_masked: "****abcd",
    api_key_hint: "末尾 abcd",
  },
  network: { proxy_url: "http://127.0.0.1:7890" },
  skills: { enabled: { pa: true, tuning: false } },
  dex: { enabled: false, slippage_bps: 50 },
};

async function mockSettings(page: Page): Promise<void> {
  await page.route("**/api/v1/settings", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(SETTINGS_FIXTURE),
    }),
  );
}

test.describe("/system/set 指针目标尺寸", () => {
  test("所有指针目标的命中区 >= 24x24", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockHealth(page);
    await mockSettings(page);

    const resp = await page.goto("/system/set");
    expect(resp?.status()).toBe(200);

    // 正向对照:关键控件必须真的渲染。少了这个断言,后端掉线/数据没到时
    // 页面只剩空壳,下面的空 offenders 就是假绿(空结果 != 没问题)。
    for (const id of ["llm-base-url", "llm-model", "ex-api-key", "net-proxy-url"]) {
      await expect(page.locator(`#${id}`)).toBeVisible({ timeout: 15000 });
    }
    // 断言 fixture 的特征值真的上了屏 —— 证明 mock 拦住了(而非碰巧连上真后端)。
    // 真后端返回的 base_url 是 http://127.0.0.1:7863/v1、provider 是 openai,
    // 与 fixture 不同;若 mock 失效这里会立刻红,不会静默退化成"测真后端"。
    await expect(page.locator("#llm-base-url")).toHaveValue(SETTINGS_FIXTURE.llm.base_url);
    await expect(page.locator("#llm-provider")).toHaveValue(SETTINGS_FIXTURE.llm.provider);
    // 分支控件:fixture 打开了 api_key_configured / smart_mode,这两个必须出现。
    await expect(page.locator("#llm-smart-strategy")).toBeVisible();
    await expect(page.getByRole("button", { name: "测试连接" })).toBeVisible();
    // 「清除」按钮只在 api_key_configured 为真时渲染。真后端 exchange.api_key_configured
    // 是 false,只有 llm 那个会出;fixture 两个都开了,所以必须正好 2 个 —— 这条
    // 同时证明 mock 生效、且两个面板的分支控件都渲染出来了。
    await expect(page.getByRole("button", { name: "清除" })).toHaveCount(2);

    const { total, offenders } = await page.evaluate(
      ({ MIN, SEL }) => {
        const describe = (el: Element): string => {
          const cls =
            typeof el.className === "string"
              ? el.className.trim().split(/\s+/).slice(0, 3).join(".")
              : "";
          return (
            el.tagName.toLowerCase() +
            (el.id ? `#${el.id}` : "") +
            (cls ? `.${cls}` : "")
          );
        };

        // 点 (x,y) 时是否落到 el 自身或其内部。伪元素不是独立节点,
        // ::before 撑开的那块会命中宿主元素,所以这条能识别出"盒子小但点得着"。
        const covers = (el: Element, x: number, y: number): boolean => {
          const hit = document.elementFromPoint(x, y);
          return !!hit && (hit === el || el.contains(hit));
        };

        const offenders: { sel: string; w: number; h: number }[] = [];
        let total = 0;

        for (const el of Array.from(document.querySelectorAll(SEL))) {
          const pre = el.getBoundingClientRect();
          if (pre.width === 0 || pre.height === 0) continue; // display:none / 未布局
          if (getComputedStyle(el).visibility === "hidden") continue;
          total++;

          if (pre.width >= MIN && pre.height >= MIN) continue;

          // 元素可能在视口外,elementFromPoint 会返回 null。先滚到中间再量。
          el.scrollIntoView({ block: "center", behavior: "instant" });
          const b = el.getBoundingClientRect();
          const cx = b.x + b.width / 2;
          const cy = b.y + b.height / 2;
          // 采样"以元素中心为准的 24x24 盒"的五个点(中心 + 四角)。四角要落在元素
          // 真的能接住点击的地方,才算它在各方向上都有 24px 命中区。角点取 24/2 再
          // 内缩 0.5px,避开 elementFromPoint 在边界上的归属歧义。
          // 注意不能按"补足 24px 还差几像素"去采样 —— 那个内缩量小于元素自身半高时,
          // 采样点仍落在元素盒内,恒命中自己(第一版就是这么写成假门的)。
          const R = MIN / 2 - 0.5;
          const probes: [number, number][] = [
            [cx, cy],
            [cx - R, cy - R],
            [cx + R, cy - R],
            [cx - R, cy + R],
            [cx + R, cy + R],
          ];

          const inViewport = probes.every(
            ([x, y]) => x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight,
          );
          // 滚到中心后仍在视口外 = 量不准。fail-closed(记为 offender),不静默放过。
          if (!inViewport || !probes.every(([x, y]) => covers(el, x, y))) {
            offenders.push({
              sel: describe(el) + (inViewport ? "" : " (视口外,无法判定)"),
              w: Math.round(b.width * 100) / 100,
              h: Math.round(b.height * 100) / 100,
            });
          }
        }

        return { total, offenders };
      },
      { MIN, SEL: TARGET_SELECTOR },
    );

    // 正向对照之二:控件数量下限。防"页面渲染了一半"也算过。
    expect(total, "指针目标太少,页面可能没渲染完整").toBeGreaterThanOrEqual(30);

    expect(
      offenders,
      `${offenders.length} 个指针目标命中区 < ${MIN}x${MIN}px:\n` +
        offenders.map((o) => `  ${o.w}x${o.h}  ${o.sel}`).join("\n"),
    ).toEqual([]);

    // 顺带锁住同一批修复里的另一个缺陷:嵌套对象被 String() 落成 "[object Object]"。
    await expect(page.locator("body")).not.toContainText("[object Object]");
  });
});
