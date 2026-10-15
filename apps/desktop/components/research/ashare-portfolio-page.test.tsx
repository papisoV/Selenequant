import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AsharePortfolioPage } from "./ashare-portfolio-page";

vi.mock("@/lib/api-client", () => ({
  apiDirectRaw: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      readonly status: number,
    ) {
      super(message);
    }
  },
}));

import { apiDirectRaw } from "@/lib/api-client";
const mockApiDirect = vi.mocked(apiDirectRaw);

// portfolio-backtest 成功返回裸 dict（三组 metrics + config，无 data/meta envelope）
const PORTFOLIO_PAYLOAD = {
  portfolio_metrics: {
    cumulative_return: 0.2513,
    annual_return: 0.1523,
    sharpe: 1.42,
    max_drawdown: -0.121,
    win_rate: 0.583,
    volatility: 0.068,
    avg_turnover: 0.0932,
    calmar_ratio: 1.26,
  },
  benchmark_metrics: {
    cumulative_return: 0.1024,
    annual_return: 0.0653,
    sharpe: 0.71,
    max_drawdown: -0.185,
    win_rate: 0.531,
    volatility: 0.055,
  },
  excess_metrics: {
    cumulative_return: 0.1489,
    annual_return: 0.087,
    sharpe: 0.88,
    max_drawdown: -0.064,
    win_rate: 0.602,
    volatility: 0.031,
  },
  config: { n_long: 20, rebalance_days: 5, cost_bps: 15, benchmark_index: "000300" },
};

// 零窗口/空数据：后端 404 + envelope {data: null, error: {code, message}}
const ZERO_WINDOW_ENVELOPE = {
  data: null,
  meta: { request_id: null },
  error: { code: "NOT_FOUND", message: "零个可结算窗口：检查 start_date 与基准指数日历重叠" },
};

describe("AsharePortfolioPage", () => {
  beforeEach(() => {
    mockApiDirect.mockReset();
    mockApiDirect.mockResolvedValue(PORTFOLIO_PAYLOAD);
  });

  it("运行组合回测:POST 参数拼装正确,裸 dict 三组指标渲染到三个分区", async () => {
    render(<AsharePortfolioPage />);

    fireEvent.click(screen.getByRole("button", { name: "运行组合回测" }));

    await waitFor(() => {
      expect(mockApiDirect).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/ashare/portfolio-backtest?"),
        { method: "POST" },
      );
    });
    const url = mockApiDirect.mock.calls[0][0] as string;
    expect(url).toContain("symbols=600519%2C000858%2C601318");
    expect(url).toContain("n_long=20");
    expect(url).toContain("rebalance_days=5");
    expect(url).toContain("cost_bps=15");
    expect(url).toContain("forward_col=fwd_5d");
    expect(url).toContain("benchmark_index=000300");

    // 三个分区标题
    await waitFor(() => {
      expect(screen.getByText("组合指标")).toBeTruthy();
    });
    expect(screen.getByText("基准指标")).toBeTruthy();
    expect(screen.getByText("超额指标")).toBeTruthy();

    // 组合/基准/超额各自专属数值（组合 25.13% / 基准 10.24% / 超额 14.89%）
    expect(screen.getByText("25.13%")).toBeTruthy(); // portfolio cumulative_return
    expect(screen.getByText("10.24%")).toBeTruthy(); // benchmark cumulative_return
    expect(screen.getByText("14.89%")).toBeTruthy(); // excess cumulative_return
    expect(screen.getByText("-12.10%")).toBeTruthy(); // portfolio max_drawdown
  });

  it("error 分支:mock reject 时错误信息显示,不渲染指标分区", async () => {
    mockApiDirect.mockRejectedValue(new Error("HTTP 502 EXCHANGE_ERROR"));
    render(<AsharePortfolioPage />);

    fireEvent.click(screen.getByRole("button", { name: "运行组合回测" }));

    await waitFor(() => {
      expect(screen.getByText("HTTP 502 EXCHANGE_ERROR")).toBeTruthy();
    });
    expect(screen.queryByText("组合指标")).toBeNull();
    expect(screen.queryByText("基准指标")).toBeNull();
    expect(screen.queryByText("超额指标")).toBeNull();
  });


  it("个股权重下拉:选择后请求参数带上 stock_weight,默认 equal", async () => {
    render(<AsharePortfolioPage />);
    // 默认 equal
    fireEvent.click(screen.getByRole("button", { name: "运行组合回测" }));
    await waitFor(() => expect(mockApiDirect).toHaveBeenCalled());
    expect(mockApiDirect.mock.calls[0][0]).toContain("stock_weight=equal");

    mockApiDirect.mockClear();
    mockApiDirect.mockResolvedValue(PORTFOLIO_PAYLOAD);
    fireEvent.change(screen.getByDisplayValue("等权"), {
      target: { value: "inv_vol" },
    });
    fireEvent.click(screen.getByRole("button", { name: "运行组合回测" }));
    await waitFor(() =>
      expect(mockApiDirect.mock.calls[0][0]).toContain("stock_weight=inv_vol"),
    );
  });

  it("风险平价提示:slsqp + n_long<17 时显示退回等权警告", async () => {
    render(<AsharePortfolioPage />);
    fireEvent.change(screen.getByDisplayValue("等权"), {
      target: { value: "slsqp" },
    });
    // 默认 n_long=20 → 无警告
    expect(screen.queryByText(/退回等权/)).toBeNull();
    fireEvent.change(screen.getByDisplayValue("20"), { target: { value: "5" } });
    await waitFor(() => expect(screen.getByText(/退回等权/)).toBeTruthy());
  });

  it("零窗口分支:404 envelope 的后端 message 显示,而非空指标组", async () => {
    mockApiDirect.mockResolvedValue(ZERO_WINDOW_ENVELOPE);
    render(<AsharePortfolioPage />);

    fireEvent.click(screen.getByRole("button", { name: "运行组合回测" }));

    await waitFor(() => {
      expect(screen.getByText(/零个可结算窗口/)).toBeTruthy();
    });
    expect(screen.queryByText("组合指标")).toBeNull();
    expect(screen.queryByText("基准指标")).toBeNull();
    expect(screen.queryByText("超额指标")).toBeNull();
  });
});
