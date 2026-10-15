import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// === mock api-client =================================
const apiMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  api: (url: string) => apiMock(url),
  apiPost: vi.fn(),
}));

// === mock store =================================
const { useFakeWorkflowStore } = vi.hoisted(() => {
  const { create } = require("zustand") as typeof import("zustand");
  interface FakeWorkflowState {
    setOptimization: (r: unknown) => void;
  }
  return {
    useFakeWorkflowStore: create<FakeWorkflowState>(() => ({
      setOptimization: vi.fn(),
    })),
  };
});

vi.mock("@/lib/store/workflow", () => ({
  useWorkflowStore: useFakeWorkflowStore,
}));

// === mock next/navigation =================================
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import { TuningResultsPanel } from "@/components/research/tuning-results-panel";
import type { TuningResultSummary } from "@/lib/tuning-bridge";

const base = (over: Partial<TuningResultSummary>): TuningResultSummary => ({
  file_name: "tuning_20261002_000001.json",
  timestamp: "20261002_000001",
  strategy_id: "bb_v3",
  strategy_name: "布林带升级版",
  interval: "1d",
  exchange: "binance",
  market_type: "futures",
  symbol: "BTC/USDT",
  best_sharpe: 2.1,
  best_params: { bb_len: 20 },
  total_return: 0.5,
  max_drawdown: 0.1,
  win_rate: 0.6,
  n_trials: 30,
  optimization_time: 12.5,
  ...over,
});

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue([]);
});

describe("TuningResultsPanel 失效标注 (US-003)", () => {
  it("AC3-5a: 含 pre_noop_fix 行 → 警示条文案出现 + 行内「修复前」Badge", async () => {
    apiMock.mockResolvedValue([
      base({ pre_noop_fix: true, timestamp: "20260825_141110" }),
      base({ strategy_id: "bb_v4", file_name: "tuning_20261002_000002.json" }),
    ]);
    render(<TuningResultsPanel />);
    expect(
      await screen.findByText(/优化器采样修复前的历史榜单/)
    ).toBeInTheDocument();
    expect(screen.getByText("修复前")).toBeInTheDocument();
  });

  it("AC3-5b: 全 false → 无警示条无 Badge", async () => {
    apiMock.mockResolvedValue([base({ pre_noop_fix: false })]);
    render(<TuningResultsPanel />);
    await screen.findByText(/共 1 条调参记录/);
    expect(
      screen.queryByText(/优化器采样修复前的历史榜单/)
    ).not.toBeInTheDocument();
    expect(screen.queryByText("修复前")).not.toBeInTheDocument();
  });

  it("AC3-5c: 空结果 → 不渲染警示条,空态提示正常", async () => {
    apiMock.mockResolvedValue([]);
    render(<TuningResultsPanel />);
    expect(await screen.findByText(/暂无调参结果/)).toBeInTheDocument();
    expect(
      screen.queryByText(/优化器采样修复前的历史榜单/)
    ).not.toBeInTheDocument();
  });
});
