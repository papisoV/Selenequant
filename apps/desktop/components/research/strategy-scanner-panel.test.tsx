import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

/**
 * 策略扫描面板的显示契约。
 *
 * 守的是「不伪造」：扫描期间后端只回 status/progress，没有任何指标字段。
 * 面板必须据此显示「还没有结果」，而不是补 0 后再按 >=0 涂成绿色 +0.0%。
 */
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/store/workflow", () => ({
  useWorkflowStore: (sel: (s: { setOptimization: () => void }) => unknown) =>
    sel({ setOptimization: vi.fn() }),
}));

const { apiMock, apiPostMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  apiPostMock: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
}));

import { StrategyScannerPanel } from "./strategy-scanner-panel";

const TEMPLATES = [
  { id: "ma-cross", name: "MA Cross", name_zh: "均线交叉", category: "trend" },
];

/** api() 同时服务 factory-templates 与 backtest 轮询，按 path 分流 */
function routeApi(handler: (path: string) => unknown) {
  apiMock.mockImplementation(async (path: string, ...rest: unknown[]) => {
    void rest;
    if (path === "/api/v1/strategies/factory-templates") return TEMPLATES;
    return handler(path);
  });
}

async function renderPanel() {
  const utils = render(<StrategyScannerPanel />);
  // 模板加载是 effect 里的异步
  await act(async () => {
    await Promise.resolve();
  });
  return utils;
}

/** 勾上唯一模板后点「开始扫描」 */
async function startScan() {
  fireEvent.click(screen.getByLabelText(/均线交叉|MA Cross/));
  // 模板卡片是 Checkbox + label；若 label 关联不成立则退回点 checkbox
  const start = await screen.findByRole("button", { name: /开始扫描/ });
  fireEvent.click(start);
}

beforeEach(() => {
  apiMock.mockReset();
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ task_id: "bt-1" });
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("扫描期间不伪造结果", () => {
  it("后端一直回 running 时，表里不出现任何结果行，也不显示「已完成 1」", async () => {
    routeApi(() => ({ status: "running", progress: 40 }));
    await renderPanel();
    await startScan();

    // 推进几轮轮询
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });

    expect(screen.getByText(/已完成 0 \/ 1/)).toBeInTheDocument();
    expect(screen.queryByText(/共 1 个有效结果/)).not.toBeInTheDocument();
    // 扫描中应给出「结果表稍后才出现」的解释
    expect(screen.getByText(/结果表会在任务完成后出现/)).toBeInTheDocument();
  });

  it("只有 status=completed 才落结果行", async () => {
    routeApi(() => ({
      status: "completed",
      task_id: "bt-1",
      total_return: 0.12,
      sharpe_ratio: 1.5,
      max_drawdown: 0.08,
      win_rate: 0.55,
      total_trades: 42,
      profit_factor: 1.8,
    }));
    await renderPanel();
    await startScan();

    const cell = await screen.findByText("+12.0%");
    expect(cell).toBeInTheDocument();
    expect(cell.className).toContain("text-emerald-500");
    expect(screen.getByText(/共 1 个有效结果/)).toBeInTheDocument();
  });

  it("完成但缺 total_return 时渲染中性 —，且不带涨跌色", async () => {
    routeApi(() => ({
      status: "completed",
      task_id: "bt-1",
      sharpe_ratio: 1.5,
      max_drawdown: 0.08,
      win_rate: 0.55,
      total_trades: 42,
      profit_factor: 1.8,
    }));
    await renderPanel();
    await startScan();

    await screen.findByText(/共 1 个有效结果/);

    // 收益列渲染 —，不是 +0.0%
    expect(screen.queryByText("+0.0%")).not.toBeInTheDocument();
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
    for (const d of dashes) {
      expect(d.className).toContain("text-ink-4");
      expect(d.className).not.toContain("text-emerald-500");
      expect(d.className).not.toContain("text-red-500");
    }
  });

  it("进度来自后端 progress，不是已完成数 / 总数", async () => {
    routeApi(() => ({ status: "running", progress: 40 }));
    const { container } = await renderPanel();
    await startScan();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    // 已完成 0 / 1，但进度条必须显示 40%
    expect(screen.getByText(/已完成 0 \/ 1/)).toBeInTheDocument();
    // Radix Progress 的填充值只体现在 indicator 的 transform 上（组件不设 aria-valuenow）
    const root = container.querySelector('[role="progressbar"]') as HTMLElement | null;
    expect(root).not.toBeNull();
    const indicator = root!.firstElementChild as HTMLElement;
    expect(indicator.style.transform).toBe("translateX(-60%)");
  });

  it("停止按钮真的停：点了之后不再发新的 POST，未完成标记为已取消而不是失败", async () => {
    routeApi(() => ({ status: "running", progress: 10 }));
    await renderPanel();
    await startScan();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    const stop = screen.getByRole("button", { name: /停止/ });
    fireEvent.click(stop);
    const postsAfterStop = apiPostMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(apiPostMock.mock.calls.length).toBe(postsAfterStop);
    expect(screen.getByText(/已取消 1 \/ 1/)).toBeInTheDocument();
    expect(screen.queryByText(/^失败 1/)).not.toBeInTheDocument();
  });

  it("progress 长时间不动会显示「最后进展 N 秒前」与停滞标记", async () => {
    routeApi(() => ({ status: "running", progress: 30 }));
    await renderPanel();
    await startScan();

    // 默认 stallAfterMs = 120s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(130_000);
    });

    expect(screen.getByText(/最后进展 \d+ 秒前/)).toBeInTheDocument();
    expect(screen.getByText("停滞")).toBeInTheDocument();
  });
});
