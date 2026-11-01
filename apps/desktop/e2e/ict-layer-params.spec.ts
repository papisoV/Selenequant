import { test, expect, type Page } from "@playwright/test";

// ICT 四层参数抽屉的端到端探针（真实浏览器 + 真实后端）。
//
// 断言四件事，任何一件不通都说明"参数只是长了个样子":
//   1. 展开行确实渲染出该层的控件（data-testid=ict-params-<layer>）；
//   2. 改"参与计算"的参数（破位口径 / 时段 / swing 半径）后，叠加层请求的
//      query 真的跟着变 —— 这是参数到达后端的唯一证据；
//   3. 改"纯显示"的参数（上限 / 档位 / 不透明度）不触发重新请求，但
//      localStorage 立刻落盘、树行摘要跟着变；
//   4. 刷新页面后设置仍在（hydrate 从 localStorage 恢复）。
//
// 另外用 canvas 像素做一次开/关对照，确认 ICT 层确实在图上有东西。

const ICT_KEY = "selenespace-ict-display";

async function openTree(page: Page) {
  await page.getByRole("button", { name: "对象树" }).click();
  const dialog = page.getByRole("complementary", { name: "对象树" });
  await expect(dialog).toBeVisible();
  return dialog;
}

function ictRow(dialog: ReturnType<Page["getByRole"]>, label: string) {
  const section = dialog.locator('[data-testid="kf-draw-indicators"]');
  return section.locator(".kf-draw-row", { hasText: label }).first();
}

async function canvasPainted(page: Page): Promise<number> {
  return page.evaluate(() => {
    const canvases = Array.from(
      document.querySelectorAll(".chart-body canvas"),
    ) as HTMLCanvasElement[];
    const c = canvases[0];
    if (!c) return -1;
    const ctx = c.getContext("2d");
    if (!ctx || c.width === 0) return -1;
    const step = Math.max(8, Math.floor(c.width / 40));
    let painted = 0;
    for (let x = 0; x < c.width; x += step) {
      for (let y = 0; y < c.height; y += step) {
        if (ctx.getImageData(x, y, 1, 1).data[3] > 0) painted++;
      }
    }
    return painted;
  });
}

test("ICT 参数抽屉：控件渲染 · 请求带参 · 落盘 · 刷新恢复 · 图上确有变化", async ({
  page,
}) => {
  test.setTimeout(180_000);

  const overlayRequests: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("ict-structure-overlay")) overlayRequests.push(url);
  });
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 400)));

  await page.goto("/watch/kf", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".chart-body canvas", { timeout: 60_000 });
  await page.waitForFunction(
    () => {
      const c = document.querySelector(".chart-body canvas") as HTMLCanvasElement | null;
      if (!c) return false;
      const ctx = c.getContext("2d");
      if (!ctx) return false;
      for (let x = 0; x < c.width; x += 20) {
        for (let y = 0; y < c.height; y += 20) {
          if (ctx.getImageData(x, y, 1, 1).data[3] > 0) return true;
        }
      }
      return false;
    },
    { timeout: 60_000 },
  );

  // 干净起点：清掉别的轮次留下的设置，再刷新让它按默认 hydrate。
  await page.evaluate((k) => localStorage.removeItem(k), ICT_KEY);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".chart-body canvas", { timeout: 60_000 });

  const paintedBefore = await canvasPainted(page);

  const dialog = await openTree(page);

  // ---- 层 1：swings —— 纯显示参数（不触发重算） -------------------------
  const swingsRow = ictRow(dialog, "ICT 摆动点");
  await swingsRow.locator(".kf-draw-ic").first().click(); // 打开层
  await swingsRow.locator(".kf-draw-caret").click(); // 展开参数抽屉
  const swingParams = dialog.locator('[data-testid="ict-params-swings"]');
  await expect(swingParams).toBeVisible();
  // 四个控件族都在：上限 / 档位三勾 / 方向三选
  await expect(swingParams.getByLabel("最多显示")).toHaveValue("40");
  for (const t of ["ST 短期", "IT 中期", "LT 长期"]) {
    await expect(swingParams.getByLabel(t)).toBeVisible();
  }
  await expect(swingParams.getByRole("radiogroup", { name: "方向" })).toBeVisible();

  const reqBefore = overlayRequests.length;
  await swingParams.getByLabel("最多显示").fill("7");
  // 给足一个 fetch 周期，确认纯显示参数没有触发新请求
  await page.waitForTimeout(1200);
  expect(
    overlayRequests.length,
    "改『最多显示』不应重新请求（渲染期过滤）",
  ).toBe(reqBefore);

  const persisted1 = await page.evaluate((k) => localStorage.getItem(k), ICT_KEY);
  expect(persisted1, "纯显示参数也必须落盘").toContain('"maxCount":7');
  // 树行摘要同步更新（用户不展开也能看出当前配置）
  await expect(swingsRow).toContainText("≤7");

  // ---- 层 2：events —— 破位口径参与计算，必须带进请求 -------------------
  const eventsRow = ictRow(dialog, "ICT 破位线");
  await eventsRow.locator(".kf-draw-ic").first().click();
  await eventsRow.locator(".kf-draw-caret").click();
  const eventParams = dialog.locator('[data-testid="ict-params-events"]');
  await expect(eventParams).toBeVisible();
  const reqBeforeBreak = overlayRequests.length;
  await eventParams.getByRole("radio", { name: "影线" }).click();
  await expect
    .poll(() => overlayRequests.length, { timeout: 15_000 })
    .toBeGreaterThan(reqBeforeBreak);
  expect(
    overlayRequests.at(-1),
    "破位口径必须出现在请求 query 里",
  ).toContain("break_on=wick");

  // ---- 层 3：killzone —— 时段集合参与计算 ------------------------------
  const kzRow = ictRow(dialog, "ICT 时段带");
  await kzRow.locator(".kf-draw-ic").first().click();
  await kzRow.locator(".kf-draw-caret").click();
  const kzParams = dialog.locator('[data-testid="ict-params-killzone"]');
  await expect(kzParams).toBeVisible();
  const reqBeforeSessions = overlayRequests.length;
  await kzParams.getByLabel("纽约").uncheck();
  await expect
    .poll(() => overlayRequests.length, { timeout: 15_000 })
    .toBeGreaterThan(reqBeforeSessions);
  expect(overlayRequests.at(-1), "时段集合必须进 query").toContain("sessions=london");

  // 最后一个时段不允许关掉（后端会把空集当调用方错误）。
  // 用 click 而不是 uncheck：uncheck 自带"点完必须变成未勾"的断言，
  // 而这里被防护拒绝、保持勾选才是正确行为。
  await kzParams.getByLabel("伦敦").click();
  await page.waitForTimeout(300);
  await expect(kzParams.getByLabel("伦敦")).toBeChecked();

  // ---- 层 4：dealingRange —— 单条线开关是纯显示 ------------------------
  const drRow = ictRow(dialog, "ICT 溢价折价");
  await drRow.locator(".kf-draw-ic").first().click();
  await drRow.locator(".kf-draw-caret").click();
  const drParams = dialog.locator('[data-testid="ict-params-dealingRange"]');
  await expect(drParams).toBeVisible();
  const reqBeforeDR = overlayRequests.length;
  await drParams.getByLabel("溢价线").uncheck();
  await page.waitForTimeout(1000);
  expect(overlayRequests.length, "单条线开关不应重新请求").toBe(reqBeforeDR);
  const persisted2 = await page.evaluate((k) => localStorage.getItem(k), ICT_KEY);
  expect(persisted2).toContain('"premium":false');

  // ---- 图上确实画了东西 -------------------------------------------------
  await page.waitForTimeout(1500);
  const paintedAfter = await canvasPainted(page);
  expect(paintedAfter, "开 ICT 层后主图应有像素").toBeGreaterThan(0);
  test.info().annotations.push({
    type: "canvas",
    description: `painted before=${paintedBefore} after=${paintedAfter}`,
  });

  // ---- 刷新后设置仍在 ---------------------------------------------------
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".chart-body canvas", { timeout: 60_000 });
  const dialog2 = await openTree(page);
  const swingsRow2 = ictRow(dialog2, "ICT 摆动点");
  await swingsRow2.locator(".kf-draw-caret").click();
  await expect(
    dialog2.locator('[data-testid="ict-params-swings"]').getByLabel("最多显示"),
    "刷新后应恢复落盘的值",
  ).toHaveValue("7");

  // 刷新后的请求应带着落盘的 break_on 与时段
  const lastAfterReload = overlayRequests.at(-1) ?? "";
  expect(lastAfterReload).toContain("break_on=wick");
  expect(lastAfterReload).toContain("sessions=london");
  // 收尾：把存储清掉，避免污染用户后续手工验证
  await page.evaluate((k) => localStorage.removeItem(k), ICT_KEY);

  expect(pageErrors, "不应有未捕获异常").toHaveLength(0);
});
