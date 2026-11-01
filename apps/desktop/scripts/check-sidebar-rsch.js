// 验证 sidebar 入口修复 + rsch 真参数
const { chromium } = require("playwright");

(async () => {
  const b = await chromium.launch();
  const page = await b.newPage({ viewport: { width: 1366, height: 820 } });
  let pass = 0, fail = 0;
  const ok = (n, c) => { c ? pass++ : fail++; console.log(`  ${c ? "PASS" : "FAIL"} ${n}`); };

  // 1) sidebar AI 组织链三步
  await page.goto("http://localhost:3000/research/today", { waitUntil: "networkidle", timeout: 60000 });
  const chain = await page.evaluate(() => {
    const titles = [...document.querySelectorAll(".nav-chain-title")];
    const aiOrg = titles.find((t) => t.textContent?.includes("AI 组织"));
    if (!aiOrg) return { found: false, body: null };
    const chainBody = aiOrg.nextElementSibling;
    return { found: true, body: chainBody ? chainBody.textContent : null };
  });
  console.log("[sidebar AI 组织链]");
  ok("AI 组织链存在", chain.found);
  ok("含 TODAY", !!chain.body?.includes("TODAY"));
  ok("含 Mission", !!chain.body?.includes("Mission"));
  ok("含 策略驾驶舱", !!chain.body?.includes("策略驾驶舱"));
  // "更多"组不再包含三页
  const moreCnt = await page.evaluate(() => {
    const t = [...document.querySelectorAll(".more-title")];
    return t.length ? t[0].parentElement?.textContent || "" : "";
  });
  ok("更多组不再含 today/missions/cockpit", !moreCnt.includes("TODAY · 值班室") && !moreCnt.includes("策略驾驶舱"));

  // 2) rsch 真参数
  await page.goto("http://localhost:3000/research/rsch", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(1000);
  const rsch = await page.evaluate(() => document.body.textContent || "");
  console.log("[rsch 真参数]");
  ok("无 param1 假参数", !rsch.includes("param1") && !rsch.includes("param2"));
  ok("真参数出现(sma_short 等)", rsch.includes("sma_short") || rsch.includes("ema_fast") || rsch.includes("adx_period"));
  ok("中文名出现", rsch.includes("均线交叉") || rsch.includes("趋势跟踪"));

  await b.close();
  console.log(`\n==== ${pass} pass / ${fail} fail ====`);
  process.exit(fail > 0 ? 1 : 0);
})();
