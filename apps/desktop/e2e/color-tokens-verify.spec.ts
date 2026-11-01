import { test, expect, type Page } from "@playwright/test";

// P0-A 语义色拆分 — browser 实测 spec(plan §4 硬门:验收必含 getComputedStyle 实测,禁纯静态推理)
//
// 纪律:
//   - 禁截图 — 全部 DOM getComputedStyle 断言
//   - 禁固定 sleep — locale 等待一律 waitForFunction(dataset.locale)
//   - .vhero-kill 只读断言,绝不点击(点击会真 POST /api/v1/trading/kill-switch 平仓)
//   - 数据依赖行(撤单/平仓按钮仅在 paper store 有行时渲染)缺失时 test.skip + TODO,不伪造
//
// 状态标注(2026-10-03 作者注):
//   - [step1-已落地] 判定轴恒定/方向轴翻转/.vhero-kill destructive 迁移 — Step 1 已改 globals.css,
//     本 spec 在 Step 3 落地后由 lead 首跑即可全绿(现仍可跑,test 2/3 依赖 E2E 环境有无持仓数据)
//   - [step3-待验] 撤单/平仓按钮的原子类(text-neg/border-neg/40)尚未迁移(Step 3 范围)。
//     按 plan §4 实测注记,这两个原子类从未生效(被 unlayered .row-btn/.row-btn.danger 压制),
//     故本 spec 对它们的断言锚定在"级联终局"而非原子类字面——Step 3 迁移后断言应原样通过;
//     若实测值与预期不符 = 级联复活/预期有误信号,如实记录上报,不许改断言迁就。

const BASE = process.env.E2E_PORT ? `http://localhost:${process.env.E2E_PORT}` : "http://localhost:3000";

// locale 机制(2026-10-03 lead 实测修正):
//   ❌ addInitScript 种 localStorage 在本 app 失效 —— I18nProvider 两个 effect 竞态:
//      effect A 读 localStorage→setLocale, effect B 同时用当前 "en" 回写 localStorage + dataset.locale;
//      dev(StrictMode)双调用 effect 下, 第二次读到的已是被覆盖的 "en" → 种子被冲掉。
//      (这是 app 的 dev 期 locale 初始化缺陷, 非本批范围; 生产无 StrictMode 可能正常)
//   ✅ setAttribute 后置 — 本 spec 全部断言都是**纯 CSS 级**(读 --color-* computed / DOM 元素的类着色),
//      不依赖 React locale state, 正合 Architect design review的"setAttribute 仅用于纯 CSS 断言"边界。
//      (需要 React state 联动重绘的图表断言属 Step 2 范围, 不在本 spec)
async function gotoWithLocale(page: Page, path: string, locale: "en" | "zh-CN") {
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  // ⚠️ 必须等 hydration 完成再 setAttribute:provider 挂载后会在 effect 里写 dataset.locale,
  //    若抢在 hydration 前 set,provider 的 effect 会把它覆写回默认 en(实测踩过)。
  //    判据:provider 已写入 dataset.locale(非 null)= 挂载 effect 已跑过。
  await page.waitForFunction(
    () => document.documentElement.dataset.locale !== null,
    undefined,
    { timeout: 15_000 }
  );
  // hydration 后的稳定窗口:等一帧确保 provider effect 全部完成(禁固定长 sleep,用 rAF 双帧)
  await page.evaluate(
    () => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())))
  );
  await page.evaluate((loc) => {
    document.documentElement.dataset.locale = loc;
  }, locale);
  // 等 CSS 重算:断言某一依赖变量已到位(zh 态 pos 必为红系声明值)
  await page.waitForFunction(
    (loc) => document.documentElement.dataset.locale === loc,
    locale,
    { timeout: 5_000 }
  );
}

// 自定义属性 getPropertyValue 返回声明值:hex(#61bc66)或 var() 链解析后的 rgb。
// 两种形态都解析;解析失败返回 null,由调用方显式 fail(禁静默跳过——否则翻转断言形同虚设)。
function parseRgb(s: string): [number, number, number] | null {
  const rgb = s.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  const hex = s.match(/^#([0-9a-fA-F]{6})$/);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }
  return null;
}

const DESTRUCTIVE_RGB = [243, 97, 92]; // #f3615c — 判定轴固定红,恒定不翻转
const SUCCESS_RGB = [97, 188, 102]; // #61bc66 — 判定轴固定绿,恒定不翻转
const BORDER_SOFT_RGB = [20, 24, 28]; // #14181c — .row-btn border 简写的级联终局

test.describe("P0-A 语义色双轴 browser 实测", () => {
  test("判定轴恒定 + 方向轴翻转:destructive/success 两 locale 同值,pos/neg 互翻 (AC-A/AC-D)", async ({
    page,
  }) => {
    // en 态(显式种子,不依赖默认值)
    await gotoWithLocale(page, "/watch/kf", "en");
    const en = await page.evaluate(() => {
      const cs = getComputedStyle(document.documentElement);
      return {
        destructive: cs.getPropertyValue("--color-destructive").trim(),
        success: cs.getPropertyValue("--color-success").trim(),
        pos: cs.getPropertyValue("--color-pos").trim(),
        neg: cs.getPropertyValue("--color-neg").trim(),
      };
    });

    // zh 态(重新 goto,种子 zh-CN)
    await gotoWithLocale(page, "/watch/kf", "zh-CN");
    const zh = await page.evaluate(() => {
      const cs = getComputedStyle(document.documentElement);
      return {
        destructive: cs.getPropertyValue("--color-destructive").trim(),
        success: cs.getPropertyValue("--color-success").trim(),
        pos: cs.getPropertyValue("--color-pos").trim(),
        neg: cs.getPropertyValue("--color-neg").trim(),
      };
    });

    // 判定轴:两态恒定(同字符串 + 解析后同 RGB)
    expect(zh.destructive).toBe(en.destructive);
    expect(zh.success).toBe(en.success);
    const enDes = parseRgb(en.destructive);
    const zhDes = parseRgb(zh.destructive);
    const enSuc = parseRgb(en.success);
    const zhSuc = parseRgb(zh.success);
    expect(enDes).toEqual(DESTRUCTIVE_RGB);
    expect(zhDes).toEqual(DESTRUCTIVE_RGB);
    expect(enSuc).toEqual(SUCCESS_RGB);
    expect(zhSuc).toEqual(SUCCESS_RGB);

    // 方向轴:两态翻转且方向正确(en 绿涨 G>R / zh 红涨 R>G),neg 互为镜像
    expect(zh.pos).not.toBe(en.pos);
    expect(zh.neg).not.toBe(en.neg);
    const enPos = parseRgb(en.pos);
    const zhPos = parseRgb(zh.pos);
    expect(enPos).not.toBeNull();
    expect(zhPos).not.toBeNull();
    if (enPos && zhPos) {
      expect(enPos[1]).toBeGreaterThan(enPos[0]); // en: pos 绿,G > R
      expect(zhPos[0]).toBeGreaterThan(zhPos[1]); // zh: pos 红,R > G
    }
    expect(zh.pos).toBe(en.neg); // zh 的 pos 值 = en 的 neg 值(整族镜像)
  });

  test("级联遮蔽实测(合成探针):row-btn.danger 的级联终局 = destructive 红 + border-soft 灰 (§4)", async ({
    page,
  }) => {
    // 真按钮仅在有持仓/挂单数据时渲染(数据依赖)。本测试用**同 className 合成探针**
    // 复现级联裁决:CSS 层叠只看类组合,同 className 的节点 cascade 终局一致。
    // 明确标注:这是层叠等价探针,非冒充真按钮;真按钮断言在下面两个测试(数据缺失则 skip)。
    // classList 逐字复制 position-table-embedded.tsx:151/:166 的撤单/平仓按钮。
    await gotoWithLocale(page, "/watch/kf", "zh-CN");
    const probe = await page.evaluate(() => {
      const el = document.createElement("button");
      // :151/:166 原样:row-btn danger + 原子类(Step 3 将迁移 text-neg→text-destructive 等;
      // 本断言锚定级联终局,迁移后原子类变化不影响本 spec)
      el.className = "row-btn danger rounded border border-neg/40 px-1.5 py-0.5 text-[9px] text-neg";
      document.body.appendChild(el);
      const cs = getComputedStyle(el);
      const out = { color: cs.color, borderColor: cs.borderColor };
      el.remove();
      return out;
    });

    // color 终局:unlayered .row-btn.danger{color:var(--color-destructive)} 压制 layered text-neg
    expect(parseRgb(probe.color)).toEqual(DESTRUCTIVE_RGB);
    // borderColor 终局:unlayered .row-btn{border:1px solid var(--color-border-soft)} 压制 border-neg/40
    // ⚠️ 若实测非此灰 = "级联复活"(有东西被顺带清了)或预期有误 → 如实记录上报,不许改断言迁就
    expect(parseRgb(probe.borderColor)).toEqual(BORDER_SOFT_RGB);
  });

  test("平仓按钮(open tab,真按钮):computed color = destructive 红,zh-CN 下不翻绿 (§4)", async ({
    page,
  }) => {
    await gotoWithLocale(page, "/trade/pos", "zh-CN");
    const closeBtn = page.locator("button.row-btn.danger", { hasText: "平仓" });
    try {
      await closeBtn.first().waitFor({ state: "visible", timeout: 5000 });
    } catch {
      // TODO(E2E 造数):paper store 无 open 持仓时按钮不渲染;造数后去掉 skip 跑真按钮断言
      test.skip(true, "E2E 环境无 open 持仓,平仓按钮未渲染(数据依赖)— 合成探针已覆盖层叠终局");
    }
    const styles = await closeBtn.first().evaluate((el) => {
      const cs = getComputedStyle(el);
      return { color: cs.color, borderColor: cs.borderColor };
    });
    expect(parseRgb(styles.color)).toEqual(DESTRUCTIVE_RGB);
    expect(parseRgb(styles.borderColor)).toEqual(BORDER_SOFT_RGB);
  });

  test("撤单按钮(pending tab,真按钮):切换挂单 tab 后 computed color = destructive 红 (§4)", async ({
    page,
  }) => {
    await gotoWithLocale(page, "/trade/pos", "zh-CN");
    // tab 标签是硬编码中文(position-table-embedded.tsx:103),与 locale 无关
    await page.locator("button.pos-tab", { hasText: "挂单" }).click();
    const cancelBtn = page.locator("button.row-btn.danger", { hasText: "撤单" });
    try {
      await cancelBtn.first().waitFor({ state: "visible", timeout: 5000 });
    } catch {
      // TODO(E2E 造数):无 pending 挂单时按钮不渲染
      test.skip(true, "E2E 环境无 pending 挂单,撤单按钮未渲染(数据依赖)— 合成探针已覆盖层叠终局");
    }
    const styles = await cancelBtn.first().evaluate((el) => {
      const cs = getComputedStyle(el);
      return { color: cs.color, borderColor: cs.borderColor };
    });
    expect(parseRgb(styles.color)).toEqual(DESTRUCTIVE_RGB);
    expect(parseRgb(styles.borderColor)).toEqual(BORDER_SOFT_RGB);
  });

  test("kill switch 安全级(只读):zh-CN 下 .vhero-kill 字色为红系,绝不点击 (D1)", async ({
    page,
  }) => {
    // ⚠️ 只读断言。该按钮真 POST /api/v1/trading/kill-switch(持久化激活+阻塞下单),禁止 click。
    // D1 场景:迁移前 .vhero-kill 用 --color-neg,zh 下翻绿误导不可逆操作;迁移后 destructive 恒红。
    await gotoWithLocale(page, "/watch/kf", "zh-CN");

    // DashboardLayout → VHero → .vhero-kill,场景路由全局挂载;/watch/kf 必有
    // dev 首编译慢,给足等待(实测后端就绪后仍在 ~3s 内出现)
    const kill = page.locator(".vhero-kill");
    await kill.waitFor({ state: "visible", timeout: 20_000 });

    const colors = await page.evaluate(() => {
      const svg = document.querySelector<HTMLElement>(".vhero-kill svg");
      const label = document.querySelector<HTMLElement>(".vhero-kill .vk-label");
      return {
        svg: svg ? getComputedStyle(svg).color : null,
        label: label ? getComputedStyle(label).color : null,
      };
    });

    // zh-CN 下(svg 常态无 hover)两处字色都应是 destructive 红(R>G);若为绿系 = D1 回归信号
    for (const [key, value] of Object.entries(colors)) {
      const rgb = value ? parseRgb(value) : null;
      expect(rgb, `${key} 色值可解析(实际: ${value})`).not.toBeNull();
      if (rgb) {
        expect(rgb[0], `${key} R 分量(红系,实际: ${value})`).toBeGreaterThan(rgb[1]);
      }
    }
    // 精确值:应等于 destructive #f3615c(不宽松到"任意红",该面只此一色)
    expect(parseRgb(colors.svg ?? "")).toEqual(DESTRUCTIVE_RGB);
    expect(parseRgb(colors.label ?? "")).toEqual(DESTRUCTIVE_RGB);
  });
});
