import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import type { ICPanelReport } from "@/lib/api/market-analysis";

const { icPanelMock, mineICPanelMock } = vi.hoisted(() => ({
  icPanelMock: vi.fn(),
  mineICPanelMock: vi.fn(),
}));
vi.mock("@/lib/api/market-analysis", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/market-analysis")>(
    "@/lib/api/market-analysis",
  );
  return { ...actual, getICPanel: icPanelMock, mineICPanel: mineICPanelMock };
});

import { ICDiagnosticCard } from "@/components/research/ic-diagnostic-card";

const report = (): ICPanelReport => ({
  status: "ok",
  snapshot: {
    episode_id: "ep-1",
    occurred_at: "2026-09-25T12:30:00+00:00",
    boundary_start: "2026-08-01T00:00:00+00:00",
    boundary_end: "2026-09-01T00:00:00+00:00",
    n_rows: 2160,
    target_definition: "close[t+1]/close[t] - 1",
  },
  symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  interval: "1h",
  factor: "momentum",
  period: 5,
  rolling_ic: { dates: ["2026-08-01", "2026-08-02"], values: [0.3, -0.2] },
  rolling_ir: { dates: ["2026-08-01", "2026-08-02"], values: [1.2, 0.9] },
  autocorr: { dates: ["2026-08-01", "2026-08-02"], values: [-0.1, 0.05] },
  ic_distribution: { "Mean IC": 0.05, "IR": 1.1, "Std": 0.2, "t-stat": 2.5 },
  qq_stats: { r_value: 0.98, normality_p: 0.4 },
  monthly_heatmap: { years: ["2026"], months: ["Jan"], z: [[0.1]] },
  sector_ic: { groups: ["btc"], contribs: [0.2] },
});

beforeEach(() => {
  icPanelMock.mockReset();
  mineICPanelMock.mockReset();
});

describe("ICDiagnosticCard IC 诊断面板", () => {
  it("渲染统计 + IC/IR 轨迹 + 累计 IC + 自相关 + 月度热图 + QQ 诊断", async () => {
    icPanelMock.mockResolvedValue(report());
    render(<ICDiagnosticCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText("0.0500")).toBeInTheDocument()); // Mean IC
    expect(screen.getByText("2.50")).toBeInTheDocument(); // t-stat
    // 图表 SVG role=img
    expect(screen.getByRole("img", { name: "IC / Rolling IR" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Cumulative IC" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Self-Correlation" })).toBeInTheDocument();
    // 月度热图：年份 + 月份标题
    expect(screen.getByText("2026")).toBeInTheDocument();
    expect(screen.getByText("Jan")).toBeInTheDocument();
    // QQ 正态诊断
    expect(screen.getByText(/r = 0\.98/)).toBeInTheDocument();
    expect(screen.getByText(/D'Agostino-Pearson p = 0\.400/)).toBeInTheDocument();
    // 快照契约:血缘章 ok 态常显
    expect(screen.getByText(/快照 2026-09-25 12:30/)).toBeInTheDocument();
    // target 契约(刀4):评估定义随数据走,卡头可见
    expect(screen.getByText("· close[t+1]/close[t] - 1")).toBeInTheDocument();
  });

  it("快照存在但滚动序列为空 → 数据不足空态(与 no_snapshot 区分)", async () => {
    const empty = report();
    empty.rolling_ic = { dates: [], values: [] };
    icPanelMock.mockResolvedValue(empty);
    render(<ICDiagnosticCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText(/快照存在但数据不足/)).toBeInTheDocument());
    // 空数据态不画图(拒绝模拟)
    expect(screen.queryByRole("img", { name: "IC / Rolling IR" })).not.toBeInTheDocument();
  });

  it("no_snapshot → 立即计算按钮;点击走 POST mine 并渲染结果", async () => {
    const none = { ...report(), status: "no_snapshot" as const, snapshot: undefined };
    icPanelMock.mockResolvedValue(none);
    mineICPanelMock.mockResolvedValue(report());
    render(<ICDiagnosticCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText(/该参数集暂无因子评估快照/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /立即计算/ }));
    await waitFor(() => expect(mineICPanelMock).toHaveBeenCalledWith({ symbols: "BTC/USDT,ETH/USDT,SOL/USDT", interval: "1h" }));
    // mine 成功 → 渲染数据 + 血缘章
    await waitFor(() => expect(screen.getByText("0.0500")).toBeInTheDocument());
    expect(screen.getByText(/快照 2026-09-25 12:30/)).toBeInTheDocument();
  });

  it("命中态点刷新 = POST mine 真刷新(不再重读 GET)", async () => {
    icPanelMock.mockResolvedValue(report());
    mineICPanelMock.mockResolvedValue(report());
    render(<ICDiagnosticCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText("0.0500")).toBeInTheDocument());
    const callsBefore = icPanelMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(mineICPanelMock).toHaveBeenCalled());
    expect(icPanelMock.mock.calls.length).toBe(callsBefore);
  });

  it("mine 失败显示独立错误态(不覆盖已有报告)", async () => {
    icPanelMock.mockResolvedValue(report());
    mineICPanelMock.mockRejectedValue(new Error("kline source unavailable"));
    render(<ICDiagnosticCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText("0.0500")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(screen.getByText(/快照计算失败：kline source unavailable/)).toBeInTheDocument());
  });

  it("symbols/interval 变化时重新拉取", async () => {
    icPanelMock.mockResolvedValue(report());
    const { rerender } = render(<ICDiagnosticCard symbols="BTC/USDT,ETH/USDT,SOL/USDT" interval="1h" />);
    await waitFor(() =>
      expect(icPanelMock).toHaveBeenCalledWith(
        expect.objectContaining({ symbols: "BTC/USDT,ETH/USDT,SOL/USDT", interval: "1h" }),
      ),
    );

    rerender(<ICDiagnosticCard symbols="ETH/USDT,SOL/USDT" interval="4h" />);
    await waitFor(() =>
      expect(icPanelMock).toHaveBeenCalledWith(
        expect.objectContaining({ symbols: "ETH/USDT,SOL/USDT", interval: "4h" }),
      ),
    );
  });
});
