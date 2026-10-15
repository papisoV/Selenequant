// MISSION 视觉稿 v2 无截图健康检查(非产品代码,零 PNG)
const { chromium } = require("playwright");

const URL = "file:///<local-path-redacted>";
const STATES = ["investigation", "needs_input", "validation", "resolved"];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console.error: ${m.text()}`); });

  await page.goto(URL);
  await page.waitForTimeout(300);

  const visible = (sel) => page.$eval(sel, (el) => el.offsetParent !== null).catch(() => false);
  const countVisible = (sel) => page.$$eval(sel, (els) => els.filter((e) => e.offsetParent !== null).length);
  const topOf = (sel) => page.$eval(sel, (el) => el.getBoundingClientRect().top).catch(() => NaN);

  const results = [];
  const check = (name, ok) => { results.push(`${ok ? "PASS" : "FAIL"} ${name}`); if (!ok) process.exitCode = 1; };

  // 0. 初始状态
  check("初始 data-state=needs_input", (await page.getAttribute("html", "data-state")) === "needs_input");

  // 0.5 壳结构(对齐真实 app:VHero+Sidebar+main 三列)
  check("壳:app-shell 存在", await visible(".app-shell"));
  check("壳:sb-item active = AI 组织 · Mission",
    await page.$eval(".sb-item.active", (el) => el.textContent.includes("Mission")).catch(() => false));
  check("壳:feed-bar 底部锚定", await page.$eval(".feed-bar", (el) => {
    const r = el.getBoundingClientRect();
    return Math.abs(r.bottom - window.innerHeight) < 2;
  }));
  check("壳:scene-scroll 可滚动", await page.$eval(".scene-scroll", (el) => el.scrollHeight >= el.clientHeight));
  const sbW = await page.$eval(".sb-nav", (el) => el.getBoundingClientRect().width);
  check("壳:sidebar 宽 ≈200px", Math.abs(sbW - 200) < 4);

  for (const s of STATES) {
    await page.click(`#draft-toggle button[data-s="${s}"]`);
    await page.waitForTimeout(120);

    // NOW BAR:恰好一个 dot + 一句 sentence + 一个 anchor 可见
    check(`[${s}] now-dot 恰 1 可见`, (await countVisible(".now-bar .now-dot")) === 1);
    check(`[${s}] now-sentence 恰 1 可见`, (await countVisible(".now-bar .now-sentence")) === 1);
    check(`[${s}] now-anchor 恰 1 可见`, (await countVisible(".now-bar .now-anchor")) === 1);
    // Now Bar 在状态带下方、三栏上方
    const nb = await topOf(".now-bar"), cols = await topOf(".columns");
    check(`[${s}] now-bar 位于三栏上方`, nb < cols);
  }

  // 1. 时态顺序:Pulse(现在刚发生) → 三栏 → NEXT
  const pulse = await topOf(".pulse-zone"), cols2 = await topOf(".columns"), next = await topOf(".next-zone");
  check("pulse 顶部位置 < 三栏(flex order 生效)", pulse < cols2);
  check("三栏 < next-zone", cols2 < next);

  // 2. needs_input:四按钮 + 判断时间锚 + 时间线不含"已批准"行
  await page.click(`#draft-toggle button[data-s="needs_input"]`);
  await page.waitForTimeout(120);
  check("needs_input 四动作按钮", (await countVisible(".next-actions .btn")) === 4);
  check("needs_input 判断时间锚可见", await visible(".when-anchor"));
  check("needs_input 时间线无 user 批准行(诚实)", (await countVisible(".tl-row.t-user")) === 0);
  check("needs_input 时间线恰 4 条(恒显)", (await countVisible(".tl-row")) === 4);

  // 3. investigation:Active Work 真活清单(✓/●/○)
  await page.click(`#draft-toggle button[data-s="investigation"]`);
  await page.waitForTimeout(120);
  check("investigation work-item 存在", (await countVisible(".work-item")) >= 3);
  check("investigation live 项呼吸点", (await countVisible(".work-item.w-live")) === 1);
  check("investigation queue 项", (await countVisible(".work-item.w-queue")) === 1);

  // 4. validation:实验活行 + E4 未现
  await page.click(`#draft-toggle button[data-s="validation"]`);
  await page.waitForTimeout(120);
  check("validation 实验活行 w-live", (await countVisible(".st-validation .work-item.w-live")) === 1);
  check("validation E4 不可见(未结案)", !(await visible(".ev-only-resolved")));
  check("validation 时间线含 user 批准行", (await countVisible(".tl-row.t-user")) === 1);

  // 5. resolved:结案连锁 + E4 + 全 6 条时间线
  await page.click(`#draft-toggle button[data-s="resolved"]`);
  await page.waitForTimeout(120);
  check("resolved 结案连锁 ≥6 步", (await countVisible(".chain .c-step")) >= 6);
  check("resolved E4 可见", await visible(".ev-only-resolved"));
  check("resolved 时间线 6 条", (await countVisible(".tl-row")) === 6);

  check("零 pageerror / console.error", errors.length === 0);
  if (errors.length) errors.forEach((e) => console.log("  " + e));

  console.log(results.join("\n"));
  console.log(`\n${results.filter((r) => r.startsWith("PASS")).length}/${results.length} passed`);
  await browser.close();
})();
