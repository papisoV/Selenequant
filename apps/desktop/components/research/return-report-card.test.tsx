import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReturnReport } from "@/lib/api/market-analysis";

const { returnReportMock } = vi.hoisted(() => ({ returnReportMock: vi.fn() }));
vi.mock("@/lib/api/market-analysis", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/market-analysis")>(
    "@/lib/api/market-analysis",
  );
  return { ...actual, getReturnReport: returnReportMock };
});

import { ReturnReportCard } from "@/components/research/return-report-card";

const report = (): ReturnReport => ({
  symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  interval: "1h",
  factor: "momentum",
  period: 5,
  n_bins: 5,
  cum_ret_ls: { dates: ["2026-08-01", "2026-08-02"], values: [0.01, 0.03] },
  cum_ret_net_ls: { dates: ["2026-08-01", "2026-08-02"], values: [0.008, 0.025] },
  turnover_ls: { dates: ["2026-08-01", "2026-08-02"], values: [0.5, 0.6] },
  avg_turnover_ls: 0.55,
  mean_ret: 0.02,
  stats_ls: { "Ann. Return": 0.15, "Ann. Sharpe": 1.8, "Max Drawdown": -0.12, "WinRate": 0.6 },
  df_indus: { industries: ["DeFi", "L1"], returns: [0.03, -0.02] },
});

beforeEach(() => {
  returnReportMock.mockReset();
});

describe("ReturnReportCard 多空回报面板", () => {
  it("渲染统计 + 累计多空回报双线 + 换手（含均值线）+ 行业归因", async () => {
    returnReportMock.mockResolvedValue(report());
    render(<ReturnReportCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText("15.0%")).toBeInTheDocument()); // Ann. Return
    expect(screen.getByText("1.80")).toBeInTheDocument(); // Ann. Sharpe
    expect(screen.getByText("-12.0%")).toBeInTheDocument(); // Max Drawdown
    expect(screen.getByText("60.0%")).toBeInTheDocument(); // WinRate
    // 累计双线（毛 + 净）与换手（含 Mean Turnover 均值线）
    expect(screen.getByRole("img", { name: "Return Before Cost / Cumulative LS Return" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "LS Portfolio Turnover / Mean Turnover" })).toBeInTheDocument();
    // 行业归因
    expect(screen.getByText(/行业归因/)).toBeInTheDocument();
    expect(screen.getByText("DeFi")).toBeInTheDocument();
    expect(screen.getByText("L1")).toBeInTheDocument();
    expect(screen.getByText("0.030")).toBeInTheDocument();
    expect(screen.getByText("-0.020")).toBeInTheDocument();
  });

  it("df_indus=null 时不渲染行业归因", async () => {
    const r = report();
    r.df_indus = null;
    returnReportMock.mockResolvedValue(r);
    render(<ReturnReportCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText("15.0%")).toBeInTheDocument());
    expect(screen.queryByText(/行业归因/)).not.toBeInTheDocument();
  });

  it("空滚动序列显示空态", async () => {
    const empty = report();
    empty.cum_ret_ls = { dates: [], values: [] };
    returnReportMock.mockResolvedValue(empty);
    render(<ReturnReportCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText(/暂无足够回测数据/)).toBeInTheDocument());
  });

  it("symbols/interval 变化时重新拉取", async () => {
    returnReportMock.mockResolvedValue(report());
    const { rerender } = render(<ReturnReportCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);
    await waitFor(() =>
      expect(returnReportMock).toHaveBeenCalledWith(
        expect.objectContaining({ symbols: "BTC/USDT,ETH/USDT,SOL/USDT", interval: "1h" }),
      ),
    );

    rerender(<ReturnReportCard symbols="ETH/USDT,SOL/USDT" interval="4h" />);
    await waitFor(() =>
      expect(returnReportMock).toHaveBeenCalledWith(
        expect.objectContaining({ symbols: "ETH/USDT,SOL/USDT", interval: "4h" }),
      ),
    );
  });
});
