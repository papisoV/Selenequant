// Absorbed from tick-stock-panel (MIT License)
// Copyright (c) 2026 tick-stock-panel contributors
// Source: https://github.com/shy3130/tick-stock-panel
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api-client";

// 把端点客户端整体替掉:本文件测的是面板对响应的渲染与条件映射,
// 不进网络。
const screenMock = vi.fn();
vi.mock("@/lib/api/ashare-screen", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/ashare-screen")>(
    "@/lib/api/ashare-screen",
  );
  return {
    ...actual,
    screenAshare: (...args: unknown[]) => screenMock(...args),
  };
});

import { AshareScreenPanel } from "./ashare-screen-panel";

function row(over: Record<string, unknown> = {}) {
  return {
    symbol: "600000.SH",
    name: "浦发银行",
    last: 10.0,
    prev_close: 9.9,
    high: 10.2,
    low: 9.8,
    volume: 1e6,
    amount: null,
    change_pct: 1.01,
    boards: 0,
    volume_ratio: 1.2,
    turnover: 1.0e7,
    turnover_est: true,
    float_mv: 3.0e11,
    total_mv: 3.2e11,
    limit_up: false,
    limit_down: false,
    broken: false,
    ma5: 9.9,
    ma20: 9.8,
    ma60: 9.7,
    high60: 10.5,
    low60: 8.0,
    ...over,
  };
}

function payload(over: Record<string, unknown> = {}) {
  return {
    status: "ok",
    reason: null,
    as_of: "2026-10-19",
    adjust: "raw",
    quote_source: "daily",
    coverage: {
      quoted: 3973,
      universe: 5574,
      ratio: 0.713,
      baseline_symbols: 5564,
      baseline_as_of: "2026-10-16",
      baseline_sessions: 120,
      amount_available: false,
      market_cap_available: true,
    },
    screen: {},
    matched: 1,
    scanned: 3973,
    truncated: false,
    notes: [],
    rows: [row()],
    ...over,
  };
}

async function run() {
  fireEvent.click(screen.getByTestId("screen-run"));
  await waitFor(() => expect(screenMock).toHaveBeenCalled());
  // 等结果落定(按钮从「筛选中…」恢复)
  await waitFor(() =>
    expect(screen.getByTestId("screen-run").textContent).toBe("重新筛选"),
  );
}

describe("AshareScreenPanel", () => {
  beforeEach(() => {
    screenMock.mockReset();
  });

  it("does not hit the market until the button is clicked", () => {
    // 冷路径是全市场读 + 基线装配(实测 21–29 秒)。进页面就跑会拖住整页。
    render(<AshareScreenPanel />);
    expect(screenMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("screen-idle")).toBeTruthy();
  });

  it("renders matched rows with percent-unit change and an estimated-turnover mark", async () => {
    screenMock.mockResolvedValue(payload());
    render(<AshareScreenPanel />);
    await run();

    expect(screen.getByTestId("screen-summary").textContent).toContain("命中 1 只 · 截面 3973");
    // 百分数口径:1.01 → +1.01%(不是 101%)
    expect(screen.getByText("+1.01%")).toBeTruthy();
    expect(screen.getByTestId("screen-turnover-est").textContent).toBe("估");
    // 日线回退必须对读者可见
    expect(screen.getByTestId("screen-summary").textContent).toContain("日线回退");
    // 截面日期:这一屏的 as_of 就是结果对应哪一天。变异实测:改成 null 原本零红。
    expect(screen.getByText("截至 2026-10-19")).toBeTruthy();
  });

  it("distinguishes ok+0 from an empty cross-section", async () => {
    // "没有票满足条件" 与 "截面没取到" 是两件事。ok+0 绝不能渲染成数据故障文案。
    screenMock.mockResolvedValue(payload({ matched: 0, rows: [] }));
    render(<AshareScreenPanel />);
    await run();

    expect(screen.getByTestId("screen-no-match")).toBeTruthy();
    expect(screen.queryByTestId("screen-degraded")).toBeNull();
  });

  it("reports an empty cross-section as a data problem, not 'no match'", async () => {
    screenMock.mockResolvedValue(
      payload({
        status: "empty",
        reason: "no_quotes",
        matched: 0,
        scanned: 0,
        rows: [],
      }),
    );
    render(<AshareScreenPanel />);
    await run();

    expect(screen.getByTestId("screen-degraded").textContent).toContain("不是「没有票满足条件」");
    expect(screen.queryByTestId("screen-no-match")).toBeNull();
  });

  it("sends only the filters that were filled in", async () => {
    // 空输入 = 不限制。把「没填」当 0 发出去会筛出空集。
    screenMock.mockResolvedValue(payload());
    render(<AshareScreenPanel />);

    fireEvent.change(screen.getByTestId("screen-change-min"), { target: { value: "5" } });
    fireEvent.change(screen.getByTestId("screen-float-mv-min"), { target: { value: "50" } });
    fireEvent.click(screen.getByTestId("screen-exclude-st"));
    fireEvent.click(screen.getByTestId("screen-board-cyb"));
    await run();

    const sent = screenMock.mock.calls[0][0];
    expect(sent.change_pct_min).toBe(5);
    // 亿 → 元:50 亿 = 5e9
    expect(sent.float_mv_min).toBe(5e9);
    expect(sent.exclude_st).toBe(true);
    expect(sent.board_types).toEqual(["cyb"]);
    expect(sent).not.toHaveProperty("change_pct_max");
    expect(sent).not.toHaveProperty("price_min");
    expect(sent).not.toHaveProperty("above_ma");
  });

  it("surfaces the server's 422 verdict verbatim", async () => {
    screenMock.mockRejectedValue(new ApiError("筛选条件不合法: price_min > price_max", 422));
    render(<AshareScreenPanel />);
    fireEvent.click(screen.getByTestId("screen-run"));

    await waitFor(() =>
      expect(screen.getByTestId("screen-error").textContent).toBe(
        "筛选条件不合法: price_min > price_max",
      ),
    );
  });

  it("marks a truncated result instead of silently showing a shorter list", async () => {
    screenMock.mockResolvedValue(payload({ matched: 400, truncated: true }));
    render(<AshareScreenPanel />);
    await run();

    expect(screen.getByTestId("screen-summary").textContent).toContain("仅显示前 1 条");
  });

  it("states the two turnover bases so the reader knows which one they see", async () => {
    screenMock.mockResolvedValue(payload());
    render(<AshareScreenPanel />);
    await run();

    // 日线回退 ⇒ 估算口径;快照 ⇒ 真实值。两者不能同时出现。
    expect(screen.getByTestId("screen-basis").textContent).toContain("收盘价×成交量 估算值");
    expect(screen.getByTestId("screen-basis").textContent).not.toContain("快照真实值");
  });

  it("does not cry 'market cap unavailable' when no cap filter was used", async () => {
    // 渲染实证抓到的缺陷:后端过去把「没去取表」也报成 false,于是每一次
    // 普通筛选的页脚都写「市值表不可用」——把「没问」说成「问了但坏了」。
    screenMock.mockResolvedValue(
      payload({
        coverage: { ...(payload().coverage as object), market_cap_available: null },
      }),
    );
    render(<AshareScreenPanel />);
    await run();

    expect(screen.getByTestId("screen-basis").textContent).not.toContain("市值表不可用");
  });

  it("does warn when a cap filter was used but the frame was missing", async () => {
    screenMock.mockResolvedValue(
      payload({
        coverage: { ...(payload().coverage as object), market_cap_available: false },
        matched: 0,
        rows: [],
      }),
    );
    render(<AshareScreenPanel />);
    await run();

    expect(screen.getByTestId("screen-basis").textContent).toContain("市值表不可用");
  });
});
