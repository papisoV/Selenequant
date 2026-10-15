import { test, expect, type Page, type Route } from "@playwright/test";

// US-304 产品化小项 e2e：演示回放 + CSV 导出（mocked API，无后端依赖）
// - 历史记录列表（演示回放列表）刷新 → 显示 mock 记录
// - 回放 → 渲染 Stage1/Stage2 + 交易倾向 4 档派生
// - CSV 导出 → 触发后端 export.csv 请求 + 浏览器下载
// - 交易倾向 + 等待收盘提示（派生，不新增 contract 字段）

const BASE = "http://127.0.0.1:3000";

const mockRecord = {
  meta: {
    record_id: "ar-e2e1",
    timestamp: "2026-08-12T00:00:00Z",
    input_id: "in-e2e",
    session_id: "s-e2e",
  },
  input_data: {
    exchange: "binance",
    market_type: "futures",
    symbol: "BTC/USDT",
    interval: "1h",
    klines: [
      { timestamp: 1755000000000, open: 60000, high: 60100, low: 59900, close: 60050, volume: 100 },
      { timestamp: 1755003600000, open: 60050, high: 60200, low: 60000, close: 60150, volume: 120 },
    ],
  },
  stage1: {
    market_regime: "trend_up",
    entry_gate: "proceed",
    signal_patterns: [{ name: "trend-follow", bias: "bullish", confidence: 0.8 }],
    trend_context: {},
    bar_features: {},
    evidence: [{ kind: "kline", key: "higher-highs", statement: "close rising" }],
    diagnosis_summary: { bias: "bullish" },
  },
  stage2: {
    decision: "market",
    dispatch_playbooks: ["playbook-1"],
    decision_summary: { bias: "bullish" },
    evidence: [],
  },
  dispatch_playbooks: [],
  usage_total: { prompt_tokens: 10, completion_tokens: 5 },
};

async function mockMarketAnalysis(page: Page): Promise<{ csvRequested: () => boolean }> {
  let csvHit = false;
  await page.route(/\/api\/v1\/market-analysis\/records\?limit=20$/, (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ records: [mockRecord] }) }),
  );
  await page.route(/\/api\/v1\/market-analysis\/records\/ar-e2e1$/, (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockRecord) }),
  );
  await page.route(/\/api\/v1\/market-analysis\/records\/ar-e2e1\/export\.csv$/, (route: Route) => {
    csvHit = true;
    route.fulfill({ status: 200, contentType: "text/csv", body: "timestamp,open,high,low,close,volume\n1,2,3,4,5,6\n" });
  });
  return { csvRequested: () => csvHit };
}

test.describe("US-304 演示回放 + 导出", () => {
  test("历史记录刷新 → 回放 → 导出 CSV + 交易倾向派生", async ({ page }) => {
    const { csvRequested } = await mockMarketAnalysis(page);

    const resp = await page.goto(`${BASE}/research/rsch`);
    expect(resp?.status()).toBe(200);
    await page.waitForLoadState("networkidle");

    // 历史记录（演示回放列表）
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.getByText("ar-e2e1", { exact: false }).first()).toBeVisible();

    // 回放 → 结果渲染（Stage 1 / Stage 2 + 交易倾向 4 档派生）
    await page.getByRole("button", { name: "回放", exact: true }).click();
    await expect(page.getByText("Stage 1 诊断").first()).toBeVisible();
    await expect(page.getByText("Stage 2 决策").first()).toBeVisible();
    // 交易倾向卡片: US-004 改为展示输入参数（默认均衡），非结果派生
    await expect(page.getByText("均衡").first()).toBeVisible();

    // CSV 导出（下载事件 + 后端请求被触发）
    const downloadPromise = page.waitForEvent("download", { timeout: 10_000 });
    await page.getByRole("button", { name: "CSV", exact: true }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("ar-e2e1.csv");
    expect(csvRequested()).toBe(true);
  });
});

// US-003 流式分析：开始分析 → POST /analyze/stream（mocked SSE）→ 结果卡片渲染。
// 逐帧增量/错误边界由 vitest 单测覆盖（原子 mock 下 React 批量渲染，progressive 断言会抖）。
test.describe("US-003 流式分析", () => {
  test("开始分析 → SSE 端点 → 结果卡片渲染（替代骨架屏）", async ({ page }) => {
    const sseBody = [
      { type: "analyze_start" },
      { type: "stage1_start", attempt: 1 },
      { type: "stage1_text", delta: "市场趋势向上" },
      { type: "stage1_done", attempt: 1, usage: { prompt_tokens: 10, completion_tokens: 5 } },
      { type: "stage2_skipped", gate: "wait" },
      { type: "analyze_done", record: mockRecord },
    ]
      .map((ev) => `data: ${JSON.stringify(ev)}\n\n`)
      .join("");

    let streamRequested = false;
    await page.route(/\/api\/v1\/market-analysis\/analyze\/stream$/, (route: Route) => {
      streamRequested = true;
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body: sseBody,
      });
    });

    await page.goto(`${BASE}/research/rsch`);
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: "开始分析", exact: true }).click();
    // 流结束 → 结果卡片（替代旧骨架屏的最终呈现）
    await expect(page.getByText("Stage 1 诊断", { exact: true }).first()).toBeVisible();
    // US-004 交易倾向卡展示输入参数（默认均衡）
    await expect(page.getByText("均衡").first()).toBeVisible();
    expect(streamRequested).toBe(true);
  });
});
