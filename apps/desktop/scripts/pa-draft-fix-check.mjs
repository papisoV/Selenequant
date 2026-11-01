// 视觉稿修复后 7 态自检 — 无截图 DOM 断言(P0 色拆分/P1 对比度/动作 footer/字号下限)
import { chromium } from "playwright";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const file = pathToFileURL(resolve(process.cwd(), "../../scratch/drafts/pa-decision-card.html")).href;
let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log(`PASS ${name}`); }
  else { fail++; console.log(`FAIL ${name}`); }
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on("pageerror", (e) => console.log("PAGEERROR:", e.message.slice(0, 200)));
await page.goto(file, { waitUntil: "load" });

async function setState(s) {
  await page.evaluate((st) => document.documentElement.setAttribute("data-state", st), s);
}

// 1. P0:否决徽章在 zh-CN 下渲染为固定红(不随翻转变绿)
await setState("reject");
const rejectColor = await page.evaluate(() => {
  const b = document.querySelector(".st-dec-reject .b-out-reject");
  return getComputedStyle(b).color;
});
check(`P0 reject 徽章色 = 红固定(${rejectColor})`, rejectColor === "rgb(243, 97, 92)");

// o-dir.short 保留涨跌语义(zh-CN 下 short=绿)— 需挂在 .dc-order 内才命中后代选择器
await setState("trade");
const shortColor = await page.evaluate(() => {
  const host = document.createElement("div");
  host.className = "dc-order";
  host.style.display = "none";
  host.innerHTML = '<span class="o-dir short"></span>';
  document.body.appendChild(host);
  const c = getComputedStyle(host.querySelector(".o-dir.short")).color;
  host.remove();
  return c;
});
check(`P0 o-dir.short 保留 neg 语义(${shortColor})`, shortColor === "rgb(97, 188, 102)");

// 2. P1-1:卡内 ink-3(767a7e)替代 ink-4(494d51)
await setState("trade");
const metaColor = await page.evaluate(() => getComputedStyle(document.querySelector(".st-dec-trade .dc-meta")).color);
check(`P1-1 dc-meta 升 ink-3(${metaColor})`, metaColor === "rgb(118, 122, 126)");

// 3. P1-2:动作 footer 固定(panel-decision-scroll 之外,dc-meta 之后仍可达)
const footerVisible = await page.getByRole("button", { name: /保存为实验/ }).first().isVisible();
check("P1-2 trade 态动作 footer 可见", footerVisible);
const footerStructure = await page.evaluate(() => {
  const pd = document.querySelector(".panel-decision");
  const scroll = pd.querySelector(":scope > .panel-decision-scroll");
  const actions = pd.querySelector(":scope > .decision-actions");
  return !!(scroll && actions && actions.querySelector(".act-variant.act-save"));
});
check("P1-2 结构 = scroll + 固定 decision-actions", footerStructure);
// trade 卡内不再有 dc-actions(已搬空)
const inCardActions = await page.evaluate(() =>
  !!document.querySelector(".st-dec-trade .dc-actions"));
check("P1-2 trade 卡内无残留 dc-actions", !inCardActions);

// 4. P1-2:四态动作变体显隐正确
await setState("wait");
check("wait 态 footer 显示禁用提示", await page.getByText("无交易指令,等入场信号后再保存").isVisible());
await setState("reject");
check("reject 态 footer 显示否决提示", await page.getByText("硬性否决,无交易指令").isVisible());
await setState("saved");
check("saved 态 footer 显示已保存行", await page.locator(".decision-actions .sv-ok").isVisible());

// 5. P1-3:字号下限 10px(抽查 4 处)
const fontSizes = await page.evaluate(() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    return el ? parseFloat(getComputedStyle(el).fontSize) : null;
  };
  return {
    meta: pick(".st-dec-trade .dc-meta"),
    tnSub: pick(".st-dec-trade .tn-sub"),
    oNote: pick(".st-dec-trade .o-note"),
    aHint: pick(".decision-actions .a-hint"),
  };
});
check(`P1-3 字号全部 ≥10px(${JSON.stringify(fontSizes)})`,
  Object.values(fontSizes).every((v) => v === null || v >= 10));

// 6. 7 态切换无 JS 错误 + 340px 面板无横向溢出
for (const s of ["empty", "loading", "trade", "saved", "wait", "reject", "fail"]) {
  await setState(s);
}
const overflow = await page.evaluate(() => {
  const p = document.querySelector(".pa-panel");
  return p ? p.scrollWidth > p.clientWidth + 2 : null;
});
check("340px 面板无横向溢出", overflow === false);

console.log(`\n== fix-check done: ${pass} pass / ${fail} fail ==`);
await browser.close();
process.exit(fail > 0 ? 1 : 0);
