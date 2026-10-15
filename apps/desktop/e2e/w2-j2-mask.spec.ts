import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * W2 J2 门:遮罩后第一屏自证「我在把一个问题交给 Selene,它在研究它」。
 *
 * 挂壳断言是 DOM 事实(页面升格进 /research/questions/ 后由 research/layout
 * 挂 DashboardLayout,Sidebar/WorkflowStepper/SymbolBar 应当存在),
 * 不靠遮罩糊。证据取 body.innerText(计划禁截图回喂)写 scratch/evidence/。
 *
 * 前置:dev server(3000)+ local-api(8088)已起;URL 需要一个真实问题 id,
 * 由 SELENE_W2_QID 环境变量传入,缺省用 URL 只带 ?q= 的诚实空态分支。
 */

const BASE = process.env.SELENE_W2_BASE_URL ?? "http://localhost:3000";
const QID = process.env.SELENE_W2_QID ?? "";
const OUT_DIR = join(process.cwd(), "..", "..", "scratch", "evidence");

const ROUND = process.env.SELENE_W2_ROUND ?? "1";

test("J2 遮罩前证据:挂壳 chrome + innerText", async ({ page }) => {
  const url = `${BASE}/research/questions${QID ? `?q=${encodeURIComponent(QID)}` : ""}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });

  // 挂壳:research 场景 layout 挂 DashboardLayout,chrome 根节点至少存在其一
  for (const chrome of ["Sidebar", "WorkflowStepper", "SymbolBar"]) {
    await expect(page.locator(`[data-testid="${chrome}"], .${chrome}`).first()).toBeVisible({ timeout: 15000 });
  }

  // 等页面主体渲染(标题或诚实空态二者其一)
  await page.waitForFunction(
    () => {
      const t = document.body.innerText;
      return t.includes("没在问任何问题") || document.querySelector('[data-testid="section-current-answer"]');
    },
    { timeout: 15000 },
  );

  // 等 recall settle:两处「正在取回历史…」全部消失(或从未出现)才取证,
  // 避免把 pending 态定格进证据。
  await page.waitForFunction(
    () => !document.body.innerText.includes("正在取回历史…"),
    { timeout: 15000 },
  );

  const innerText = await page.evaluate(() => document.body.innerText);
  mkdirSync(OUT_DIR, { recursive: true });
  const roundTag = QID ? `q-${ROUND}` : `noq-${ROUND}`;
  const outPath = join(OUT_DIR, `w2-j2-${roundTag}.txt`);
  writeFileSync(outPath, innerText, "utf-8");
  console.log(`[w2-j2] innerText written: ${outPath} (${innerText.length} chars)`);

  // 遮罩:审计通道取证时把研究面盖住,只留判定所需最小信息。遮罩不改变 DOM 事实,
  // 只在截图/取证时叠加;零 chrome 断言在遮罩前已成立。
  await page.evaluate(() => {
    const mask = document.createElement("div");
    mask.setAttribute("data-j2-mask", "");
    mask.style.cssText =
      "position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,0.92);color:#fff;font:12px/1.6 monospace;padding:24px;pointer-events:none;";
    mask.textContent = "J2 取证中 — 遮罩已施加,下方为已定格的页面状态";
    document.body.appendChild(mask);
  });

  const maskedText = await page.evaluate(() => document.body.innerText);
  expect(maskedText.length).toBeGreaterThan(0);
});
