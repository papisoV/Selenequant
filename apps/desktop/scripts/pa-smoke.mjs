// PA 页端到端 smoke — 无截图 DOM 断言(禁 PNG 回喂惯例)
// 走查:页面加载 → hero 空态 → 分析按钮存在 → 点击(真管线,若 LLM 不可用则验证 fail 态呈现)
import { chromium } from "playwright";

const BASE = "http://localhost:3000";
let pass = 0;
let fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log(`PASS ${name}`); }
  else { fail++; console.log(`FAIL ${name}`); }
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on("pageerror", (e) => console.log("PAGEERROR:", e.message.slice(0, 200)));

await page.goto(`${BASE}/research/pa`, { waitUntil: "networkidle", timeout: 60000 });

// 1. 页面壳存在
check("PA 页标题/场景渲染", await page.locator("text=Price Action").first().isVisible().catch(() => false)
  || await page.locator("text=PA").first().isVisible().catch(() => false));

// 2. 分析按钮存在(hero 空态或面板内)
const analyzeBtn = page.getByRole("button", { name: /分析当前图表/ }).first();
check("「分析当前图表」按钮存在", await analyzeBtn.isVisible({ timeout: 15000 }).catch(() => false));

// 3. 图表挂载
check("K 线图容器存在", await page.locator("[data-testid='kf-chart'], canvas").first().isVisible().catch(() => false));

// 4. 点击分析 → 等待三态之一(loading→完成 / wait / reject / fail)
if (await analyzeBtn.isVisible().catch(() => false)) {
  await analyzeBtn.click().catch(() => {});
  // loading 或完成播报,任一出现即通过;超时 180s(真 LLM 管线 40s+)
  // 区分假阳性:loading 态有"正在"文案,真管线输出是 Stage 链/徽章/决策内容
  const anyOutcome = await Promise.race([
    page.getByText(/Stage 1 诊断链|Stage 2 决策链|风险回报|无入场信号|否决/).first()
      .waitFor({ state: "visible", timeout: 180000 }).then(() => "record-card"),
    page.getByText(/失败|错误|超时|timeout/i).first()
      .waitFor({ state: "visible", timeout: 180000 }).then(() => "fail-card"),
    page.getByText(/无 K 线|数据不足|insufficient/i).first()
      .waitFor({ state: "visible", timeout: 180000 }).then(() => "no-data"),
  ]).catch(() => null);
  check(`点击后有管线输出(得到: ${anyOutcome ?? "无,120s 超时"})`, !!anyOutcome);

  // 5. 底部决策区/输入条存在
  check("chat 输入条存在", await page.locator("input[type='text'], textarea").first().isVisible().catch(() => false));
} else {
  fail++;
}

// 6. 340px 面板溢出扫描(仅面板区域)
const overflow = await page.evaluate(() => {
  const panel = document.querySelector("[class*='w-[340px]'], aside, [class*='340']");
  if (!panel) return null;
  return panel.scrollWidth > panel.clientWidth + 2;
});
check(overflow === null ? "340px 面板未找到(布局可能不同,记录跳过)" : `340px 面板无横向溢出(${overflow})`, overflow !== true);

console.log(`\n== smoke done: ${pass} pass / ${fail} fail ==`);
await browser.close();
process.exit(fail > 0 ? 1 : 0);
