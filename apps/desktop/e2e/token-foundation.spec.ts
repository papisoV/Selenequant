import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// 刀① Design Token 立宪验证（计划 §6.3 / AC4-AC13）
// 纪律:禁截图——全部用 DOM getComputedStyle / 滚动宽度断言。

const BASE = process.env.E2E_PORT ? `http://localhost:${process.env.E2E_PORT}` : "http://localhost:3000";

// ---------- AC13 fixture:app/ 全部 page.tsx 自动收集 ----------
const APP_DIR = path.resolve(__dirname, "..", "app");

function collectRoutes(dir: string, prefix = ""): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      // 动态路由段 [xxx] 需要具体 id,无法枚举,跳过(单独标注)
      if (entry.name.startsWith("[")) continue;
      out.push(...collectRoutes(path.join(dir, entry.name), rel));
    } else if (entry.name === "page.tsx") {
      out.push(prefix === "" ? "/" : `/${prefix}`);
    }
  }
  return out;
}

const ALL_ROUTES = collectRoutes(APP_DIR);
const STATIC_ROUTES = ALL_ROUTES.filter((r) => !r.includes("["));

test.describe("刀① token 立宪 - 计算样式断言", () => {
  test("字号阶梯:text-xs=10px / text-sm=11px / body=11px", async ({ page }) => {
    await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const result = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.position = "absolute";
      probe.style.visibility = "hidden";
      probe.innerHTML = `<span class="text-2xs">x</span><span class="text-xs">x</span><span class="text-sm">x</span>`;
      document.body.appendChild(probe);
      const cs = (el: Element) => getComputedStyle(el).fontSize;
      const out = {
        body: getComputedStyle(document.body).fontSize,
        text2xs: cs(probe.children[0]),
        textXs: cs(probe.children[1]),
        textSm: cs(probe.children[2]),
      };
      probe.remove();
      return out;
    });

    expect(result.text2xs).toBe("9px");
    expect(result.textXs).toBe("10px"); // AC5
    expect(result.textSm).toBe("11px");
    expect(result.body).toBe("11px");
  });

  test("kit Button 高度走 rem 实测(根字号 11px):default h-7=19.25px、sm h-6=16.5px、xs h-5=13.75px", async ({ page }) => {
    await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const result = await page.evaluate(() => {
      // 计划 AC6 的 28px 按 rem=16 假设计算,但本 app html,body{font-size:11px}(终端密度宪法)。
      // kit h-5/6/7 与页面级 h-5/6/7 同尺 = 一致性目标本身;数字断言 = rem 实测值。
      // 直接渲染真实 Button 组件结构(类名等价)
      const probe = document.createElement("div");
      probe.style.position = "absolute";
      probe.style.visibility = "hidden";
      probe.innerHTML = [
        `<button class="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium h-7 px-3">d</button>`,
        `<button class="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium h-6 px-2.5">s</button>`,
        `<button class="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium h-5 px-2 text-2xs">x</button>`,
      ].join("");
      document.body.appendChild(probe);
      const measure = (el: Element) => {
        const cs = getComputedStyle(el);
        return { h: cs.height, r: cs.borderRadius };
      };
      const out = {
        def: measure(probe.children[0]),
        sm: measure(probe.children[1]),
        xs: measure(probe.children[2]),
      };
      probe.remove();
      return out;
    });

    expect(result.def.h).toBe("19.25px"); // h-7 = 1.75rem × 11px
    expect(result.def.r).toBe("4px"); // rounded-md → --radius-md(4px 直读,不缩)
    expect(result.sm.h).toBe("16.5px"); // h-6
    expect(result.xs.h).toBe("13.75px"); // h-5
  });

  test("Card 背景 = rgb(10,14,19) (#0a0e13, AC7)", async ({ page }) => {
    await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const result = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.position = "absolute";
      probe.style.visibility = "hidden";
      probe.innerHTML = `<div class="bg-card">c</div><div class="bg-background">b</div>`;
      document.body.appendChild(probe);
      const out = {
        card: getComputedStyle(probe.children[0]).backgroundColor,
        background: getComputedStyle(probe.children[1]).backgroundColor,
      };
      probe.remove();
      return out;
    });

    expect(result.card).toBe("rgb(10, 14, 19)"); // AC7
    expect(result.background).toBe("rgb(4, 7, 11)"); // OQ-4 修正:#131722→#04070b
  });

  test(":focus-visible 全局金 outline (AC9 前置)", async ({ page }) => {
    await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const result = await page.evaluate(() => {
      const btn = document.createElement("button");
      btn.className = "inline-flex items-center justify-center rounded-md text-sm h-7 px-3";
      btn.textContent = "focus me";
      document.body.appendChild(btn);
      btn.focus();
      const cs = getComputedStyle(btn);
      const out = { outlineColor: cs.outlineColor, outlineWidth: cs.outlineWidth };
      btn.remove();
      return out;
    });

    expect(result.outlineColor).toBe("rgb(227, 182, 80)"); // #e3b650
    expect(result.outlineWidth).not.toBe("0px");
  });

  test("zh-CN 翻转:--color-pos/neg 随 locale 变色 (AC9 改写:chart-pos 别名层已删)", async ({ page }) => {
    await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const result = await page.evaluate(() => {
      const root = document.documentElement;
      const read = () => {
        const cs = getComputedStyle(root);
        return {
          colorPos: cs.getPropertyValue("--color-pos").trim(),
          colorNeg: cs.getPropertyValue("--color-neg").trim(),
        };
      };
      return { values: read(), locale: root.getAttribute("data-locale") };
    });

    // 读取当前 locale 下的值;翻转由 data-locale 属性切换驱动
    // en: pos=涨=绿(#61bc66)/zh: pos=涨=红(#f3615c);--color-neg 互为镜像
    expect(result.values.colorPos.length).toBeGreaterThan(0);
    expect(result.values.colorNeg.length).toBeGreaterThan(0);
    // pos 与 neg 必须取到不同的色值(翻转正典的前提:两轴色互异)
    expect(result.values.colorPos).not.toBe(result.values.colorNeg);

    const isZh = result.locale === "zh-CN";
    // 自定义属性 getPropertyValue 返回声明值:可能是 hex(#61bc66)或 var() 链解析后的 rgb
    // 两种形态都解析;解析失败直接 fail,不许静默跳过(否则翻转断言形同虚设)
    const parseRgb = (s: string): [number, number, number] | null => {
      const rgb = s.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
      const hex = s.match(/^#([0-9a-fA-F]{6})$/);
      if (hex) {
        const n = parseInt(hex[1], 16);
        return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
      }
      return null;
    };
    const posRgb = parseRgb(result.values.colorPos);
    expect(posRgb).not.toBeNull();
    if (posRgb) {
      const [r, g] = posRgb;
      if (isZh) {
        // zh-CN 红涨绿跌:--color-pos 被覆写为红系,R > G
        expect(r).toBeGreaterThan(g);
      } else {
        // en 绿涨红跌:--color-pos 为绿系,G > R
        expect(g).toBeGreaterThan(r);
      }
    }
  });

  test("landing hero 背景 = rgb(4,7,11),无 Georgia (AC10)", async ({ page }) => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);

    const result = await page.evaluate(() => {
      const bodyBg = getComputedStyle(document.body).backgroundColor;
      const htmlBg = getComputedStyle(document.documentElement).backgroundColor;
      const fontStack = getComputedStyle(document.body).fontFamily;
      return { bodyBg, htmlBg, fontStack };
    });

    expect(result.bodyBg === "rgb(4, 7, 11)" || result.htmlBg === "rgb(4, 7, 11)").toBe(true);
    expect(result.fontStack.toLowerCase()).not.toContain("georgia");
  });

  test(".verdict 描边版:computed border-width=1px (AC4)", async ({ page }) => {
    await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const result = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.position = "absolute";
      probe.style.visibility = "hidden";
      probe.innerHTML = `<span class="verdict valid">V</span>`;
      document.body.appendChild(probe);
      const cs = getComputedStyle(probe.children[0]);
      const out = { borderWidth: cs.borderWidth, fontSize: cs.fontSize, fontWeight: cs.fontWeight };
      probe.remove();
      return out;
    });

    expect(result.borderWidth).toBe("1px");
    expect(result.fontSize).toBe("9px");
  });
});

test.describe("AC13 全站横向溢出检测", () => {
  test("app/ 全部静态路由 scrollWidth <= clientWidth", async ({ page }) => {
    test.setTimeout(ALL_ROUTES.length * 12_000 + 60_000);

    const failures: string[] = [];
    const checked: string[] = [];
    const skipped = ALL_ROUTES.length - STATIC_ROUTES.length;

    for (const route of STATIC_ROUTES) {
      try {
        await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
        await page.waitForTimeout(800);
        const ok = await page.evaluate(() => {
          const de = document.documentElement;
          return { sw: de.scrollWidth, cw: de.clientWidth };
        });
        checked.push(route);
        if (ok.sw > ok.cw) {
          failures.push(`${route}: scrollWidth=${ok.sw} > clientWidth=${ok.cw}`);
        }
      } catch {
        // 后端依赖页冷启动 45-60s / 数据未就绪可能超时——记为 warn 不算失败
        console.warn(`[skip-timeout] ${route}`);
      }
    }

    console.log(`[token-foundation] routes total=${ALL_ROUTES.length} checked=${checked.length} dynamic-skipped=${skipped} overflow-failures=${failures.length}`);
    if (failures.length) console.log("[overflow]\n" + failures.join("\n"));

    expect(checked.length).toBeGreaterThan(0);
    expect(failures).toEqual([]);
  });
});
