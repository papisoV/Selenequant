import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * 真渲染双语巡检 —— 英文模式"处处是英文" + 英文长度不破版式。
 *
 * 与 i18n-leak-scan-all 的分工:
 *   - 那个 spec 只走**首屏**(加载后 walk 一次,不点任何东西),弹窗/下拉/校验态扫不到;
 *   - 本 spec 主动打开交互态(部署向导、预警规则编辑弹窗、资产页),并同时量:
 *       中文残留(文本 + aria-label/title/placeholder/alt)
 *       横向溢出(scrollWidth > clientWidth)
 *       被截断的可交互/表单元素
 *       行高与文字换行(NB) —— 英文比中文长,中国版式最容易在这里塌
 *
 * 产物:scratch/tmp/i18n-visual/<scene>.json + <scene>.png
 * 运行:E2E_PORT=3011 npx playwright test e2e/i18n-visual-verify.spec.ts --workers=1
 */

test.setTimeout(180_000);

const OUT_DIR = "scratch/tmp/i18n-visual";
const CJK = /[一-鿿]/;
// 语言切换按钮自身标签是目标语言名,合法保留。
const ALLOW = ["中文"];

type Snapshot = {
  scene: string;
  locale: string;
  leaks: string[];
  overflow: { sel: string; scrollW: number; clientW: number }[];
  clipped: string[];
  typography: { role: string; text: string; lines: number; lineHeight: number; fontSize: number }[];
};

async function setLocale(page: Page, locale: "en" | "zh-CN") {
  await page.addInitScript((l) => {
    window.localStorage.setItem("selenespace-locale", l);
  }, locale);
}

/** 等页面自身的 locale 完成挂载同步。 */
async function waitLocale(page: Page, locale: "en" | "zh-CN") {
  await expect(page.locator("html")).toHaveAttribute("data-locale", locale, { timeout: 60_000 });
}

async function snapshot(page: Page, scene: string): Promise<Snapshot> {
  var result: Snapshot = await page.evaluate((allow) => {
    const CJK = /[一-鿿]/;
    const leaks: string[] = [];

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
      leaks.push(`text: ${t.slice(0, 90)}`);
    }
    for (const attr of ["aria-label", "title", "placeholder", "alt"]) {
      for (const el of Array.from(document.querySelectorAll(`[${attr}]`))) {
        const v = (el.getAttribute(attr) || "").trim();
        if (!v || !CJK.test(v)) continue;
        if (allow.some((a) => v.includes(a))) continue;
        if (el.closest("[aria-hidden='true']")) continue;
        leaks.push(`[${attr}] ${v.slice(0, 90)}`);
      }
    }

    // 横向溢出:只看有排版意义的容器,排除专门做横向滚动的表(overflow-x-auto/scroll)
    // 同理排除 overflow-x:auto 的容器 —— 它是被**设计**成横向滚动的,不算破版。
    const overflow: { sel: string; scrollW: number; clientW: number }[] = [];
    const seen = new Set<Element>();
    for (const el of Array.from(document.body.querySelectorAll("div,section,main,aside,nav,header,footer,button,h1,h2,h3,p,span,li,td,th"))) {
      if (seen.has(el)) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      if (el.closest("[aria-hidden='true']")) continue;
      const ox = cs.overflowX;
      if (ox === "auto" || ox === "scroll") continue;
      if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
        const id = el.id ? `#${el.id}` : "";
        const cls = typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 3).join(".") : "";
        overflow.push({ sel: `${el.tagName.toLowerCase()}${id}${cls}`, scrollW: el.scrollWidth, clientW: el.clientWidth });
      }
    }

    // 被截断:单行且 text-overflow:ellipsis 的元素,文字确实溢出
    const clipped: string[] = [];
    for (const el of Array.from(document.querySelectorAll("*"))) {
      const cs = getComputedStyle(el as HTMLElement);
      if (cs.textOverflow !== "ellipsis") continue;
      const h = el as HTMLElement;
      if (h.scrollWidth > h.clientWidth + 1 && (h.textContent || "").trim()) {
        clipped.push(`${h.tagName.toLowerCase()}: ${(h.textContent || "").trim().slice(0, 70)}`);
      }
    }

    // 原文里换行对排版敏感的几个角色:按钮 / tab / 小标签 / 表头
    // lines 用 Range.getClientRects() 量真实文本行框 —— 元素盒高含 padding,
    // 直接 height/lineHeight 会把 padding 数成行(曾把 9px 的 "All" 报成 3 行)。
    const typography: { role: string; text: string; lines: number; lineHeight: number; fontSize: number; boxH: number; clientW: number; scrollW: number }[] = [];
    const pick = (sel: string, role: string) => {
      for (const el of Array.from(document.querySelectorAll(sel)).slice(0, 40)) {
        const h = el as HTMLElement;
        const cs = getComputedStyle(h);
        const txt = (h.textContent || "").trim();
        if (!txt || txt.length > 60) continue;
        const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
        const fs = parseFloat(cs.fontSize) || 12;
        const range = document.createRange();
        range.selectNodeContents(h);
        const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
        const top = Math.round(Math.min(...rects.map((r) => r.top)));
        const usedTop = new Set<number>();
        let lines = 0;
        for (const r of rects) {
          const k = Math.round(r.top) - top;
          if (!usedTop.has(k)) { usedTop.add(k); lines++; }
        }
        typography.push({ role, text: txt.slice(0, 40), lines, lineHeight: Math.round(lh), fontSize: Math.round(fs), boxH: Math.round(h.getBoundingClientRect().height), clientW: h.clientWidth, scrollW: h.scrollWidth });
      }
    };
    pick("button", "button");
    pick("[role=tab]", "tab");
    pick("th", "th");
    return { scene: "", locale: document.documentElement.dataset.locale || "", leaks: [...new Set(leaks)], overflow, clipped, typography };
  }, ALLOW);
  result.scene = scene;
  return result;
}

function save(snap: Snapshot) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, `${snap.scene}.json`), JSON.stringify(snap, null, 2), "utf8");
}

test.describe("i18n visual verify", () => {
  test("deploy wizard (en)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setLocale(page, "en");
    await page.goto("/research/strat?deploy=1&template=ema_cross", { waitUntil: "domcontentloaded" });
    await waitLocale(page, "en");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(1500);
    save(await snapshot(page, "deploy-wizard-en"));
    await page.screenshot({ path: path.join(OUT_DIR, "deploy-wizard-en.png") });

    // 走两步,把 Step1/Step2 的文案也一起量
    const next = dialog.getByRole("button", { name: /Next/i });
    if (await next.count()) {
      await next.first().click();
      await page.waitForTimeout(800);
      save(await snapshot(page, "deploy-wizard-en-step1"));
      await page.screenshot({ path: path.join(OUT_DIR, "deploy-wizard-en-step1.png") });
      await next.first().click();
      await page.waitForTimeout(800);
      save(await snapshot(page, "deploy-wizard-en-step2"));
      await page.screenshot({ path: path.join(OUT_DIR, "deploy-wizard-en-step2.png") });
    }
  });

  test("deploy wizard (zh baseline)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setLocale(page, "zh-CN");
    await page.goto("/research/strat?deploy=1&template=ema_cross", { waitUntil: "domcontentloaded" });
    await waitLocale(page, "zh-CN");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(1500);
    save(await snapshot(page, "deploy-wizard-zh"));
    await page.screenshot({ path: path.join(OUT_DIR, "deploy-wizard-zh.png") });
  });

  test("alert rule dialog (en + zh)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setLocale(page, "en");
    await page.goto("/watch/intelligence-alerts-rules", { waitUntil: "domcontentloaded" });
    await waitLocale(page, "en");
    await page.waitForTimeout(2500);
    save(await snapshot(page, "alert-rules-page-en"));
    await page.screenshot({ path: path.join(OUT_DIR, "alert-rules-page-en.png") });

    const create = page.getByRole("button", { name: /New Rule|Create Rule|\+/i }).first();
    if (await create.count()) {
      await create.click();
      await page.waitForTimeout(1200);
      save(await snapshot(page, "alert-rule-dialog-en"));
      await page.screenshot({ path: path.join(OUT_DIR, "alert-rule-dialog-en.png") });
      // 触发校验错误:名字留空直接保存
      const saveBtn = page.getByRole("button", { name: /Save Rule|Save/i }).first();
      if (await saveBtn.count()) {
        await saveBtn.click();
        await page.waitForTimeout(600);
        save(await snapshot(page, "alert-rule-dialog-en-validation"));
        await page.screenshot({ path: path.join(OUT_DIR, "alert-rule-dialog-en-validation.png") });
      }
    }
  });

  test("assets panel (en + zh)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setLocale(page, "en");
    await page.goto("/trade/pos", { waitUntil: "domcontentloaded" });
    await waitLocale(page, "en");
    await page.waitForTimeout(3000);
    save(await snapshot(page, "trade-pos-en"));
    await page.screenshot({ path: path.join(OUT_DIR, "trade-pos-en.png") });

    await setLocale(page, "zh-CN");
    await page.goto("/trade/pos", { waitUntil: "domcontentloaded" });
    await waitLocale(page, "zh-CN");
    await page.waitForTimeout(3000);
    save(await snapshot(page, "trade-pos-zh"));
    await page.screenshot({ path: path.join(OUT_DIR, "trade-pos-zh.png") });
  });

  test("hub tabs (en + zh baseline)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const route of ["/watch/mk", "/watch/sig", "/watch/onchain-hub", "/watch/us-radar", "/watch/derivatives-hub"]) {
      const name = route.replace(/^\//, "").replace(/\//g, "_");
      for (const loc of ["en", "zh-CN"] as const) {
        await setLocale(page, loc);
        await page.goto(route, { waitUntil: "domcontentloaded" });
        await waitLocale(page, loc);
        await page.waitForTimeout(2500);
        const suffix = loc === "en" ? "en" : "zh";
        save(await snapshot(page, `hub-${name}-${suffix}`));
        await page.screenshot({ path: path.join(OUT_DIR, `hub-${name}-${suffix}.png`) });
      }
    }
  });
});
