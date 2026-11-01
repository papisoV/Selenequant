// 对象树验收探针（1021）:实测 /watch/kf 页面上
//   第 1–2 步(合流):
//     1) 工具栏已无「指标 · N」按钮,只剩「对象 · N」;
//     2) 打开对象树 → 指标区存在,9 行,分「主图叠层 4」/「独立子窗格 5」;
//     3) 范围开关默认「只看当前图」,点击切「全部标的」且标题变化;
//     4) 树内点 BOLL 可见性 → 工具栏 BOLL 快捷开关同步 active;
//     5) 关掉 MA 快捷开关 → 树里 MA 行同步变暗(dim)。
//   第 3 步(多选):
//     6) 无选中时不出批量按钮,只有提示;
//     7) 点一行 → 已选 1,行带 sel;⌘/Ctrl 加选 → 已选 2;
//     8) Shift 扩选按**树的可见顺序**取区间;
//     9) 批量隐藏是**绝对值**(混选也全变隐藏),且选中被清空;
//    10) 批量锁定把行变 ▣ 并把对象移出选中集;
//    11) 指标行不参与多选(点了不出已选计数);
//    12) 全选受范围约束,清空选择回到无选中。
//   第 4 步(侧栏 / 搜索 / 组级开关):
//    13) 树是 `.chart-area` 的常驻侧栏(role=complementary),不再是弹层;
//    14) 点页面别处不再关闭;只有折叠按钮关;
//    15) 展开时侧栏宽度实测 268px,收起重排回两列;
//    16) 搜索按名称过滤,清空恢复;无命中给「没有匹配」空态;
//    17) 搜索不改变选中集(被筛掉的行仍被批量按钮作用);
//    18) 组级眼睛/锁:混合态 ◐、点一下统一到开,且不波及组外行。
// 输出:每项 PASS/FAIL + 读取到的实际文本,外加截图。
//
// 关于播种:第 3 步的用例需要树里**真的有绘图对象**,而探针不能靠手拖
// (坐标随数据/缩放漂移,不可复现)。所以在页面脚本之前往 localStorage
// 塞一份合法的持久化数据 —— 形状与 drawing-persist.ts 的 schema 版本 1 一致。
import { chromium } from "playwright";

const BASE = process.env.PROBE_BASE ?? "http://127.0.0.1:3077";
const OUT = process.env.PROBE_OUT ?? "<local-path-redacted>";
const STORAGE_KEY = "selenespace-fib-drawings";

/** 4 条 BTC + 1 条 ETH。label 用于在树里精确定位行。 */
const SEED = {
  version: 1,
  grids: [
    { id: "seed-a", symbol: "BTC", timeframe: "1h", label: "α", start: { time: 1700000000, price: 100 }, end: { time: 1700100000, price: 200 } },
    { id: "seed-b", symbol: "BTC", timeframe: "1h", label: "β", start: { time: 1700200000, price: 120 }, end: { time: 1700300000, price: 220 } },
    { id: "seed-c", symbol: "BTC", timeframe: "1h", label: "γ", start: { time: 1700400000, price: 140 }, end: { time: 1700500000, price: 240 } },
    { id: "seed-d", symbol: "BTC", timeframe: "1h", label: "δ", start: { time: 1700600000, price: 160 }, end: { time: 1700700000, price: 260 } },
    { id: "seed-e", symbol: "ETH", timeframe: "1h", label: "ε", start: { time: 1700800000, price: 10 }, end: { time: 1700900000, price: 20 } },
  ],
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.addInitScript(
  ([key, value]) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* 隐私模式:播种失败,第 3 步会整体 FAIL,不影响第 1–2 步 */
    }
  },
  [STORAGE_KEY, JSON.stringify(SEED)],
);

const results = [];
const rec = (name, pass, detail) => {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}  ${detail ?? ""}`);
};

await page.goto(`${BASE}/watch/kf`, { waitUntil: "domcontentloaded", timeout: 120000 });
// 等工具栏渲染（KfChart 要等数据）
await page.waitForSelector(".chart-toolbar", { timeout: 120000 });
await page.waitForTimeout(3000);

// 1) 旧「指标 · N」按钮应已消失
const legacy = await page.locator("button", { hasText: /指标 · / }).count();
const treeBtn = page.locator("button", { hasText: /对象 · / }).first();
const treeBtnText = (await treeBtn.textContent())?.trim();
rec("「指标 · N」按钮已移除", legacy === 0, `count=${legacy}`);
rec("「对象 · N」按钮存在", (await treeBtn.count()) > 0, `text=${treeBtnText}`);

// 2) 打开对象树
await treeBtn.click();
const dialog = page.getByRole("complementary", { name: "对象树" });
await dialog.waitFor({ state: "visible", timeout: 15000 });

const indSection = dialog.locator('[data-testid="kf-draw-indicators"]');
const indText = (await indSection.textContent()) ?? "";
const rows = indSection.locator(".kf-draw-ind-section .kf-draw-row");
const rowCount = await rows.count();
rec("指标区存在", (await indSection.count()) === 1, `rows=${rowCount}`);
rec("含「页面级 · 作用于全部图格」", indText.includes("页面级 · 作用于全部图格"));
rec("指标行数 = 9", rowCount === 9, `rows=${rowCount}`);
const overlay = indSection.locator(".kf-draw-ind-section").nth(0);
const pane = indSection.locator(".kf-draw-ind-section").nth(1);
rec(
  "分组 4 主图叠层 + 5 独立子窗格",
  (await overlay.locator(".kf-draw-row").count()) === 4 &&
    (await pane.locator(".kf-draw-row").count()) === 5,
  `overlay=${await overlay.locator(".kf-draw-row").count()} pane=${await pane.locator(".kf-draw-row").count()}`,
);

// 3) 范围开关
const scopeBtn = dialog.locator(".kf-draw-scope");
const scopeBefore = (await scopeBtn.textContent())?.trim();
const titleBefore = (await dialog.locator(".kf-draw-head-title").textContent())?.trim();
const pressedBefore = await scopeBtn.getAttribute("aria-pressed");
rec(
  "范围默认「只看当前图」",
  scopeBefore === "只看当前图" && pressedBefore === "false",
  `text=${scopeBefore} pressed=${pressedBefore} title=${titleBefore}`,
);
await scopeBtn.click();
await page.waitForTimeout(200);
const scopeAfter = (await scopeBtn.textContent())?.trim();
const titleAfter = (await dialog.locator(".kf-draw-head-title").textContent())?.trim();
rec(
  "点一次 → 「全部标的」且标题变化",
  scopeAfter === "全部标的" && (await scopeBtn.getAttribute("aria-pressed")) === "true" &&
    titleAfter !== titleBefore,
  `text=${scopeAfter} title=${titleAfter}`,
);
await scopeBtn.click(); // 切回当前图
await page.waitForTimeout(200);

// 4) 树内点 BOLL 可见性 → 图标翻面、行取消 dim（BOLL 没有快捷 chip,只验树内闭环）
const bollRow = indSection
  .locator(".kf-draw-row")
  .filter({ hasText: "BOLL" })
  .first();
const bollToggle = bollRow.locator(".kf-draw-ic").first();
const bollIconBefore = (await bollToggle.textContent())?.trim();
const bollDimBefore = ((await bollRow.getAttribute("class")) ?? "").includes("dim");
await bollToggle.click();
await page.waitForTimeout(300);
const bollIconAfter = (await bollToggle.textContent())?.trim();
const bollDimAfter = ((await bollRow.getAttribute("class")) ?? "").includes("dim");
rec(
  "树内开 BOLL → ◉ 且去 dim",
  bollIconBefore === "○" && bollIconAfter === "◉" && bollDimBefore && !bollDimAfter,
  `icon ${bollIconBefore}→${bollIconAfter} dim ${bollDimBefore}→${bollDimAfter}`,
);

// 5) 快捷开关 ↔ 树行同源:关掉 MA chip,树里的 MA 行同步变暗。
//    第 4 步起树是常驻侧栏,点工具栏**不再**把它关掉 —— 这正是改侧栏的理由。
const maChip = page.locator("div.chart-tool", { hasText: /^MA$/ }).first();
const maChipWasActive = ((await maChip.getAttribute("class")) ?? "").includes("active");
await maChip.click();
await page.waitForTimeout(300);
const dockStaysOpen = await dialog.isVisible();
rec("点工具栏其它开关时侧栏不关(常驻)", dockStaysOpen, `open=${dockStaysOpen}`);
const indSection2 = dialog.locator('[data-testid="kf-draw-indicators"]');
// 行 textContent 是拼接的「◉▸MA20」，不能按 /^MA/ 过滤；改按 .kf-draw-title 精确定位
const maRow = indSection2
  .locator(".kf-draw-row")
  .filter({ has: page.locator(".kf-draw-title", { hasText: /^MA$/ }) })
  .first();
const maDimAfter = ((await maRow.getAttribute("class")) ?? "").includes("dim");
rec(
  "关 MA 快捷开关 → 树里 MA 行变暗",
  maChipWasActive && dockStaysOpen && maDimAfter,
  `chipWasActive=${maChipWasActive} dockOpen=${dockStaysOpen} dim=${maDimAfter}`,
);

// 6) 树内开回 MA → 侧栏留在原地,且 MA chip 恢复 active
await maRow.locator(".kf-draw-ic").first().click();
await page.waitForTimeout(300);
const chipBack = ((await maChip.getAttribute("class")) ?? "").includes("active");
const stillOpen = await dialog.isVisible();
rec(
  "树内开回 MA → 侧栏不关,工具栏 MA chip 恢复 active",
  chipBack && stillOpen,
  `active=${chipBack} dialogOpen=${stillOpen}`,
);

// 7) 几何:指标行三列对齐 + 行高实测
// 注意:比的是**中心线**。三个控件高度不同（按钮有 1px padding、空占位是 inline-block），
// 只比 top 会把"视觉居中对齐"误判成错位。同时记录每个控件的高度以备读。
const geom = await maRow.evaluate((el) => {
  const r = el.getBoundingClientRect();
  const cells = Array.from(el.querySelectorAll(".kf-draw-ic, .kf-draw-caret")).map((c) => {
    const b = c.getBoundingClientRect();
    return {
      cls: c.className,
      top: Math.round(b.y),
      h: Math.round(b.height),
      cy: Math.round(b.y + b.height / 2),
      w: Math.round(b.width),
    };
  });
  const title = el.querySelector(".kf-draw-title")?.getBoundingClientRect();
  return {
    rowH: Math.round(r.height),
    rowCenter: Math.round(r.y + r.height / 2),
    cells,
    titleX: title ? Math.round(title.x) : null,
  };
});
const centers = geom.cells.map((c) => c.cy);
const spread = Math.max(...centers) - Math.min(...centers);
rec(
  "指标行控件垂直居中（中心线极差 ≤ 2px）",
  spread <= 2,
  `centers=[${centers.join(",")}] spread=${spread}px rowH=${geom.rowH} cells=${JSON.stringify(
    geom.cells.map((c) => `${Math.round(c.w)}x${c.h}`),
  )}`,
);

// ── 第 3 步:多选 ──────────────────────────────────────────────────────
const batchBar = dialog.locator('[data-testid="kf-draw-batch"]');
const batchCount = async () => {
  const el = batchBar.locator('[data-testid="kf-draw-batch-count"]');
  return (await el.count()) === 0 ? null : (await el.textContent())?.trim();
};
const batchBtn = (text) => batchBar.locator("button", { hasText: new RegExp(`^${text}$`) });
/** 绘图行(排除指标区)。指标行的父容器带 .kf-draw-ind。 */
const drawRows = dialog.locator(".kf-draw-group:not(.kf-draw-ind) .kf-draw-row");
const rowByLabel = (label) => drawRows.filter({ has: page.locator(".kf-draw-title", { hasText: new RegExp(`^${label}$`) }) });
const rowState = async (label) => {
  const el = rowByLabel(label).first();
  const cls = (await el.getAttribute("class")) ?? "";
  return {
    sel: cls.includes("sel"),
    dim: cls.includes("dim"),
    ariaSelected: await el.getAttribute("aria-selected"),
    icon: (await el.locator(".kf-draw-ic").first().textContent())?.trim(),
    lock: (await el.locator(".kf-draw-ic").nth(1).textContent())?.trim(),
  };
};
const settle = () => page.waitForTimeout(150);

// 播种生效:工具栏计数 = 当前标的的 4 条(取末尾数字,不依赖语言的按钮文字)
const treeBtnTail = Number((await treeBtn.textContent())?.match(/(\d+)\s*$/)?.[1] ?? -1);
rec(
  "播种生效:对象计数 = 4(当前标的 BTC)",
  treeBtnTail === 4,
  `n=${treeBtnTail} text=${(await treeBtn.textContent())?.trim()}`,
);
rec("绘图行 = 4(范围=只看当前图)", (await drawRows.count()) === 4, `rows=${await drawRows.count()}`);

// 12) 无选中:不出计数,不出批量按钮
rec(
  "无选中 → 无「已选」计数、无批量按钮",
  (await batchCount()) === null && (await batchBtn("删除").count()) === 0,
  `count=${await batchCount()} deleteBtn=${await batchBtn("删除").count()}`,
);

// 7) 点一行 → 已选 1 且行带 sel
await rowByLabel("α").first().click();
await settle();
const afterPlain = await rowState("α");
rec(
  "点「α」→ 已选 1 且该行 sel",
  (await batchCount()) === "已选 1" && afterPlain.sel && afterPlain.ariaSelected === "true",
  `count=${await batchCount()} sel=${afterPlain.sel} aria=${afterPlain.ariaSelected}`,
);

// 7) Ctrl 加选 → 已选 2
await rowByLabel("γ").first().click({ modifiers: ["Control"] });
await settle();
const afterCtrl = { a: await rowState("α"), c: await rowState("γ") };
rec(
  "⌘/Ctrl 点「γ」→ 已选 2(α+γ 同时 sel)",
  (await batchCount()) === "已选 2" && afterCtrl.a.sel && afterCtrl.c.sel,
  `count=${await batchCount()} a=${afterCtrl.a.sel} c=${afterCtrl.c.sel}`,
);

// 8) Shift 扩选:从 α 到 δ(锚点被 Ctrl 点更新为 γ,所以先重设锚点为 α)
await rowByLabel("α").first().click();
await settle();
await rowByLabel("δ").first().click({ modifiers: ["Shift"] });
await settle();
const rangeCount = await batchCount();
const rangeRows = { a: await rowState("α"), b: await rowState("β"), c: await rowState("γ"), d: await rowState("δ") };
rec(
  "Shift 点「δ」(锚点 α)→ 已选 4,区间内四行全 sel",
  rangeCount === "已选 4" && rangeRows.a.sel && rangeRows.b.sel && rangeRows.c.sel && rangeRows.d.sel,
  `count=${rangeCount} sel=${["a", "b", "c", "d"].map((k) => rangeRows[k].sel).join(",")}`,
);

// 8b) 反向区间:锚点 δ → Shift 点 β = β,γ,δ(3 项,不含 α)
await rowByLabel("δ").first().click();
await settle();
await rowByLabel("β").first().click({ modifiers: ["Shift"] });
await settle();
const rev = { a: await rowState("α"), b: await rowState("β"), c: await rowState("γ"), d: await rowState("δ") };
rec(
  "反向 Shift(锚点 δ → β)= 已选 3,α 不在区间",
  (await batchCount()) === "已选 3" && !rev.a.sel && rev.b.sel && rev.c.sel && rev.d.sel,
  `count=${await batchCount()} sel=${["a", "b", "c", "d"].map((k) => rev[k].sel).join(",")}`,
);

// 11) 指标行不参与多选:点指标行不产生选中
await batchBar.locator(".kf-draw-batch-all", { hasText: "清空选择" }).click();
await settle();
const firstIndRow = indSection2.locator(".kf-draw-row").first();
await firstIndRow.click();
await settle();
rec(
  "点指标行 → 不产生选中(仍无「已选」计数)",
  (await batchCount()) === null,
  `count=${await batchCount()}`,
);

// 9) 批量隐藏是绝对值:先混选(α 可见 + 先单独隐藏 β),再全选隐藏
await rowByLabel("β").first().locator(".kf-draw-ic").first().click(); // β → 隐藏
await settle();
const betaHidden = await rowState("β");
await batchBar.locator(".kf-draw-batch-all", { hasText: "全选" }).click();
await settle();
const selBeforeHide = await batchCount();
await batchBtn("设为隐藏").click();
await settle();
const hiddenRows = { a: await rowState("α"), b: await rowState("β"), c: await rowState("γ"), d: await rowState("δ") };
rec(
  "混选后「设为隐藏」→ 四条全 ○ 且 dim(绝对值,不是翻转)",
  selBeforeHide === "已选 4" &&
    [hiddenRows.a, hiddenRows.b, hiddenRows.c, hiddenRows.d].every((r) => r.dim && r.icon === "○"),
  `selBefore=${selBeforeHide} icons=${[hiddenRows.a, hiddenRows.b, hiddenRows.c, hiddenRows.d].map((r) => r.icon).join("")} dims=${[hiddenRows.a, hiddenRows.b, hiddenRows.c, hiddenRows.d].map((r) => r.dim).join(",")} betaWasHidden=${betaHidden.dim}`,
);
rec("批量操作后选中被清空", (await batchCount()) === null, `count=${await batchCount()}`);

// 10) 批量锁定:全选 → 锁定 → 行变 ▣ 且移出选中集
await batchBar.locator(".kf-draw-batch-all", { hasText: "全选" }).click();
await settle();
await batchBtn("设为可见").click(); // 顺带把隐藏的显示回来,方便看锁定后的视觉
await settle();
await batchBar.locator(".kf-draw-batch-all", { hasText: "全选" }).click();
await settle();
const beforeLock = await batchCount();
await batchBtn("锁定").click();
await settle();
const lockedRows = { a: await rowState("α"), b: await rowState("β") };
rec(
  "「锁定」→ 行图标 ▣ 且选中被清空",
  beforeLock === "已选 4" && lockedRows.a.lock === "▣" && lockedRows.b.lock === "▣" && (await batchCount()) === null,
  `before=${beforeLock} locks=${lockedRows.a.lock}${lockedRows.b.lock} after=${await batchCount()}`,
);

// 收尾:解锁 + 选 2 项,截图留证据
await rowByLabel("α").first().locator(".kf-draw-ic").nth(1).click();
await settle();
await rowByLabel("β").first().locator(".kf-draw-ic").nth(1).click();
await settle();
await rowByLabel("α").first().click();
await rowByLabel("γ").first().click({ modifiers: ["Control"] });
await settle();
rec(
  "截图前状态 = 已选 2(α+γ)",
  (await batchCount()) === "已选 2",
  `count=${await batchCount()}`,
);

// 选中态必须是**看得见**的:class 名谁都能挂,拿计算样式说话。
// 选中行 = 金色底 + 顶部 2px 内阴影金条;未选中行两者都不能有。
const styleOf = (label) =>
  rowByLabel(label).first().evaluate((el) => {
    const s = getComputedStyle(el);
    return { bg: s.backgroundColor, shadow: s.boxShadow };
  });
const selStyle = await styleOf("α");
const unselStyle = await styleOf("β");
const isGoldish = (bg) => {
  const m = bg.match(/\d+/g)?.map(Number) ?? [];
  return m.length >= 3 && m[0] > m[2] && m[1] > m[2]; // R,G 都高于 B = 暖色(金)
};
rec(
  "选中行有金色底 + 内阴影金条,未选中行两者皆无",
  isGoldish(selStyle.bg) &&
    selStyle.shadow !== "none" &&
    !isGoldish(unselStyle.bg) &&
    unselStyle.shadow === "none",
  `sel bg=${selStyle.bg} shadow=${selStyle.shadow} | unsel bg=${unselStyle.bg} shadow=${unselStyle.shadow}`,
);

// ── 第 4 步:侧栏 + 搜索 + 组级开关 ─────────────────────────────────────
const rect = (loc) => loc.evaluate((el) => {
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
});

// 13) 侧栏是 .chart-area 的列,与 .chart-main 并排(不是覆盖在图上)
const mainRect = await rect(page.locator(".chart-main").first());
const dockRect = await rect(dialog);
const rightRect = await rect(page.locator(".right-panel").first());
const sideBySide =
  dockRect.x >= mainRect.x + mainRect.w - 2 && dockRect.x + dockRect.w <= rightRect.x + 2;
rec(
  "侧栏与图表并排(不覆盖画布,也不压住右栏)",
  sideBySide && dockRect.w === 268,
  `main=[${mainRect.x}+${mainRect.w}] dock=[${dockRect.x}+${dockRect.w}] right.x=${rightRect.x} w=${dockRect.w}`,
);

// 14) 点页面别处(图表本体)不关闭侧栏
await page.locator(".chart-body").first().click({ position: { x: 40, y: 40 } });
await page.waitForTimeout(200);
const survivedOutsideClick = await dialog.isVisible();
rec("点图表本体(侧栏外)→ 侧栏不关", survivedOutsideClick, `open=${survivedOutsideClick}`);

// 15) 折叠按钮真的关掉,且栅格回到两列
await dialog.locator(".kf-dock-collapse").click();
await page.waitForTimeout(200);
const closed = (await dialog.count()) === 0;
const mainWidthClosed = (await rect(page.locator(".chart-main").first())).w;
await treeBtn.click();
await dialog.waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(200);
const mainWidthOpen = (await rect(page.locator(".chart-main").first())).w;
rec(
  "折叠按钮关闭侧栏;收起后图表变宽,展开后让出 268px",
  closed && mainWidthOpen < mainWidthClosed && mainWidthClosed - mainWidthOpen >= 265,
  `closed=${closed} mainOpen=${mainWidthOpen} mainClosed=${mainWidthClosed} Δ=${mainWidthClosed - mainWidthOpen}`,
);

// 侧栏展开后行还都在(播种的 4 条)
rec("重开后绘图行仍为 4", (await drawRows.count()) === 4, `rows=${await drawRows.count()}`);

// 16) 搜索按名称过滤 + 清空恢复
const search = dialog.locator('[data-testid="kf-dock-search"]');
await search.fill("α");
await settle();
const filteredNames = await drawRows.locator(".kf-draw-title").allTextContents();
rec(
  "搜索「α」→ 只剩 α",
  filteredNames.length === 1 && filteredNames[0].trim() === "α",
  `rows=[${filteredNames.map((t) => t.trim()).join(",")}]`,
);

await search.fill("zzz");
await settle();
const dockTextNoHit = (await dialog.textContent()) ?? "";
const emptyRows = await drawRows.count();
rec(
  "搜索无命中 → 0 行 + 「没有匹配「zzz」的对象。」",
  emptyRows === 0 && dockTextNoHit.includes("没有匹配「zzz」的对象。"),
  `rows=${emptyRows} text=${dockTextNoHit.includes("没有匹配「zzz」的对象。")}`,
);

await search.fill("");
await settle();
rec("清空搜索 → 4 行回来", (await drawRows.count()) === 4, `rows=${await drawRows.count()}`);

// 16b) 搜索大小写/标的命中:「btc」在「只看当前图」下仍应 4 条(都属 BTC)
await search.fill("bTc");
await settle();
rec("搜索标的「bTc」(大小写不敏感)→ 4 条 BTC", (await drawRows.count()) === 4, `rows=${await drawRows.count()}`);
await search.fill("");
await settle();

// 17) 搜索不改选中:选 2 条 → 筛到只剩 1 条 → 已选仍 2 → 批量隐藏作用于两条
await batchBar.locator(".kf-draw-batch-all").click(); // 此刻应为「清空选择」或「全选」,先统一清
await settle();
await rowByLabel("α").first().click();
await rowByLabel("β").first().click({ modifiers: ["Control"] });
await settle();
const selTwo = await batchCount();
await search.fill("α");
await settle();
const countWhileFiltered = await batchCount();
await batchBtn("设为隐藏").click();
await settle();
const hiddenBoth = (await rowState("α")).dim;
await search.fill("");
await settle();
const betaDimAfter = (await rowState("β")).dim;
rec(
  "搜索只过滤显示:筛到 1 行时「已选 2」不变,批量隐藏仍作用于两条",
  selTwo === "已选 2" && countWhileFiltered === "已选 2" && hiddenBoth && betaDimAfter,
  `sel=${selTwo} whileFiltered=${countWhileFiltered} αDim=${hiddenBoth} βDim=${betaDimAfter}`,
);
await batchBar.locator(".kf-draw-batch-all").click(); // 清空选择(批量后本来就空了,保险)
await settle();
// 上一步把 α β 都藏了 —— 组级开关要从"全开"起测,先逐行点回来。
for (const label of ["α", "β"]) {
  if ((await rowState(label)).icon === "○") {
    await rowByLabel(label).first().locator(".kf-draw-ic").first().click();
    await settle();
  }
}

// 18) 组级开关:先把 α β 分到一组,测全开/混合/全关
//     分组只能在侧栏的"新分组"输入框里建,再逐条 assignGroup —— 这里走 UI:
//     建组 → 展开 α 详情 → 选分组 → 同样处理 β。
const groupName = "探针组";
await dialog.locator(".kf-draw-newgroup .kf-draw-input").fill(groupName);
await dialog.locator(".kf-draw-newgroup .kf-draw-btn").click();
await settle();
for (const label of ["α", "β"]) {
  await rowByLabel(label).first().click();
  await settle();
  await rowByLabel(label).first().locator(".kf-draw-caret").click();
  await settle();
  const select = rowByLabel(label).first().locator("xpath=following-sibling::div[contains(@class,'kf-draw-detail')]//select").first();
  await select.selectOption({ label: groupName });
  await settle();
}
const groupHead = dialog.locator(".kf-draw-group-head").filter({ hasText: groupName }).first();
const groupEye = groupHead.locator('[data-testid="kf-group-vis"]');
const groupText = async () => ((await groupEye.textContent()) ?? "").trim();
const groupState = async () => (await groupEye.getAttribute("class")) ?? "";
rec("新建分组并归入 α β → 组头眼睛为 ◉(全开)", (await groupText()) === "◉" && (await groupHead.textContent())?.includes("2"), `eye=${await groupText()} head=${(await groupHead.textContent())?.trim()}`);

// 先让组内一条可见一条隐藏 → 混合态 ◐
await rowByLabel("β").first().locator(".kf-draw-ic").first().click();
await settle();
const mixedIcon = await groupText();
rec("组内一显一隐 → 组头 ◐(混合态),且行图标半亮", mixedIcon === "◐" && (await groupState()).includes("mixed"), `eye=${mixedIcon} cls=${await groupState()}`);

// 混合态点一下 = 统一到"开"
await groupEye.click();
await settle();
const afterUnify = await groupText();
const allVisible = (await rowState("α")).icon === "◉" && (await rowState("β")).icon === "◉";
rec("混合态点组头眼睛 → 统一到全开(不是逐条翻转)", afterUnify === "◉" && allVisible, `eye=${afterUnify} α=${(await rowState("α")).icon} β=${(await rowState("β")).icon}`);

// 全开再点 = 全关,且不波及组外的 γ δ
await groupEye.click();
await settle();
const allHidden = (await rowState("α")).icon === "○" && (await rowState("β")).icon === "○";
const outsideUntouched = (await rowState("γ")).icon === "◉" && (await rowState("δ")).icon === "◉";
rec("全开再点组头眼睛 → 组内全关,组外 γ δ 不受影响", allHidden && outsideUntouched, `α=${(await rowState("α")).icon} β=${(await rowState("β")).icon} γ=${(await rowState("γ")).icon} δ=${(await rowState("δ")).icon}`);

// 组级锁:点一下全锁 ▣,再点全解锁 ▢
const groupLock = groupHead.locator('[data-testid="kf-group-lock"]');
await groupLock.click();
await settle();
const allLocked = (await rowState("α")).lock === "▣" && (await rowState("β")).lock === "▣";
await groupLock.click();
await settle();
const allUnlocked = (await rowState("α")).lock === "▢" && (await rowState("β")).lock === "▢";
rec("组级锁:点一下组内全 ▣,再点全 ▢", allLocked && allUnlocked, `locked=${allLocked} unlocked=${allUnlocked}`);

// 收尾:组内恢复可见,收起搜索框清空,给截图一个干净状态
await groupEye.click();
await settle();
await search.fill("");

// 截图
await page.screenshot({ path: `${OUT}-tree-open.png` });
await dialog.screenshot({ path: `${OUT}-tree-panel.png` });
console.log(JSON.stringify({ geom, results }, null, 1));

await browser.close();
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} PASS`);
process.exit(failed.length ? 1 : 0);
