import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";

// useBacktestPoll 未导出 —— 通过 StrategyWorkbenchPage 间接驱动。
// useApi 按 url 分发:strategy 详情必须返回真数据否则页面早退 loading 空态。
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => {
    const table: Record<string, unknown> = {
      [`/api/v1/strategies/strat-progress-1`]: {
        id: "strat-progress-1",
        name: "Progress Probe",
        type: "sma_cross",
        symbols: ["BTC/USDT"],
        interval: "1h",
        version: 1,
        lifecycle_state: "confirmed",
        entry_rules: [],
        exit_rules: [],
        parameters: {},
      },
      "/api/v1/strategies/runner/status": { running: false, deployments: [] },
    };
    return {
      data: url ? table[url] ?? (url.startsWith("/api/v1/backtests?") ? [] : null) : null,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    };
  },
}));
// api-client 不 mock —— 真实现走 global.fetch(被 installFetchScript 接管),
// runBacktest 的 POST 才能真正发出去。
vi.mock("@/lib/store/strategy", () => ({
  useStrategyStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ connected: false, signals: [] }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams("id=strat-progress-1"),
}));
vi.mock("@/components/strategy/param-panel", () => ({
  ParamPanel: () => <div />,
}));
vi.mock("@/components/strategy/backtest-results-tabs", () => ({
  BacktestResultsTabs: () => <div />,
}));
vi.mock("@/components/strategy/runner-monitor", () => ({
  RunnerMonitorPanel: () => <div />,
}));
vi.mock("@/components/strategy/version-timeline", () => ({
  VersionTimeline: () => <div />,
}));
vi.mock("next/dynamic", () => ({
  default: () => () => <div />,
}));

import { StrategyWorkbenchPage } from "@/components/strategy/strategy-workbench";

type FetchPlan = { body: unknown; status?: number };

function installFetchScript(scripts: FetchPlan[]) {
  let call = 0;
  const fetchMock = vi.fn((url: unknown) => {
    const plan = scripts[Math.min(call, scripts.length - 1)];
    call += 1;
    return Promise.resolve({
      ok: (plan.status ?? 200) === 200,
      status: plan.status ?? 200,
      json: () => Promise.resolve(plan.body),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function apiBody(data: unknown) {
  return { data, meta: null, error: null };
}

// 定时器全 fake,手动推进 2s 轮询节拍
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function flush(times = 3) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      // 一拍 microtask 排空 + 2.5s timer 推进(轮询间隔 2s)
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(2500);
    });
  }
}

describe("回测进度轮询 (US-105)", () => {
  it("RUNNING 态轮询读到 progress → 按钮显示 回测中 NN%", async () => {
    const fetchMock = installFetchScript([
      { body: apiBody({ task_id: "bt-x" }) }, // POST /backtests → 202
      { body: apiBody({ status: "running", progress: 37 }) },
      { body: apiBody({ status: "running", progress: 82 }) },
      { body: apiBody({ status: "completed" }) },
    ]);
    render(<StrategyWorkbenchPage />);
    const runBtn = screen.getAllByRole("button", { name: "运行回测" })[0];
    await act(async () => {
      fireEvent.click(runBtn);
      await Promise.resolve();
    });
    await flush(1);
    expect(screen.getAllByRole("button", { name: /回测中 37%/i })[0]).toBeTruthy();
    await flush(1);
    expect(screen.getAllByRole("button", { name: /回测中 82%/i })[0]).toBeTruthy();
  });

  it("无 progress 字段(旧后端) → 按钮仍显示 回测中… 不崩", async () => {
    installFetchScript([
      { body: apiBody({ task_id: "bt-y" }) },
      { body: apiBody({ status: "running" }) },
      { body: apiBody({ status: "completed" }) },
    ]);
    render(<StrategyWorkbenchPage />);
    const runBtn = screen.getAllByRole("button", { name: "运行回测" })[0];
    await act(async () => {
      fireEvent.click(runBtn);
      await Promise.resolve();
    });
    await flush(1);
    expect(screen.getAllByRole("button", { name: /回测中(?! )/i })[0]).toBeTruthy();
  });

  it("progress 停滞 20s → 停滞判超时(不等满 60s)", async () => {
    installFetchScript([
      { body: apiBody({ task_id: "bt-z" }) },
      { body: apiBody({ status: "running", progress: 10 }) },
      // 之后一直卡 10%
      { body: apiBody({ status: "running", progress: 10 }) },
      { body: apiBody({ status: "running", progress: 10 }) },
    ]);
    render(<StrategyWorkbenchPage />);
    const runBtn = screen.getAllByRole("button", { name: "运行回测" })[0];
    await act(async () => {
      fireEvent.click(runBtn);
      await Promise.resolve();
    });
    await flush(2); // ~4s,进度 10% 且在动
    // 停滞检测:推进 ~24s(20s 停滞阈值 + 轮询节拍)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(24_000);
    });
    await flush(1);
    expect(
      screen.getAllByText(/回测超时|进度停滞/)[0],
    ).toBeTruthy();
  });
});
