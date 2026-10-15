import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AshareBacktestPage } from "./ashare-backtest-page";

vi.mock("@/lib/api-client", () => ({
  apiDirectRaw: vi.fn(),
}));

import { apiDirectRaw } from "@/lib/api-client";
const mockApiDirect = vi.mocked(apiDirectRaw);

// ashare router 成功返回裸 dict（{metrics, universe_note}，无 data/meta envelope）
const BACKTEST_PAYLOAD = {
  metrics: {
    cumulative_return: 0.1234,
    annual_return: 0.0891,
    sharpe: 1.23,
    max_drawdown: -0.152,
    win_rate: 0.555,
    volatility: 0.072,
    time_in_market: 0.63,
    invested_win_rate: 0.61,
    avg_turnover: 0.0841,
    calmar_ratio: 0.58,
  },
  universe_note: "universe_note: 回测使用请求传入的当前成分股列表，存在生存偏差",
};

describe("AshareBacktestPage", () => {
  beforeEach(() => {
    mockApiDirect.mockReset();
    mockApiDirect.mockResolvedValue(BACKTEST_PAYLOAD);
  });

  it("运行回测:POST 参数拼装正确,裸 dict 的 metrics 渲染到统计块", async () => {
    render(<AshareBacktestPage />);

    fireEvent.click(screen.getByRole("button", { name: "运行回测" }));

    await waitFor(() => {
      expect(mockApiDirect).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/ashare/backtest?"),
        { method: "POST" },
      );
    });
    const url = mockApiDirect.mock.calls[0][0];
    expect(url).toContain("symbols=600519%2C000858%2C601318");
    expect(url).toContain("weight_method=ir_walkforward");
    expect(url).toContain("factor_set=technical");
    expect(url).toContain("cost_bps=30");
    expect(url).toContain("n_long=5");

    // 裸 dict 直取 .metrics（apiDirect 旧路径会解 .data 得 null → 这里本该空）
    await waitFor(() => {
      expect(screen.getByText("12.34%")).toBeTruthy();
    });
    expect(screen.getByText("8.91%")).toBeTruthy(); // annual_return
    expect(screen.getByText("1.23")).toBeTruthy(); // sharpe
    expect(screen.getByText("-15.20%")).toBeTruthy(); // max_drawdown
    expect(screen.getByText("55.50%")).toBeTruthy(); // win_rate
  });

  it("回测后无内联错误文案", async () => {
    render(<AshareBacktestPage />);
    fireEvent.click(screen.getByRole("button", { name: "运行回测" }));
    await waitFor(() => {
      expect(screen.getByText("12.34%")).toBeTruthy();
    });
    expect(screen.queryByText(/HTTP \d+/)).toBeNull();
  });
});
