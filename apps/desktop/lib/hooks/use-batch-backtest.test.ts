import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import {
  PollTimeoutError,
  generateScannerTasks,
  getTopResults,
  type ScannerTask,
} from "./use-batch-backtest";

// 这组测试守的不是「hook 能跑」，而是「它不会把还没算完的回测报成已完成」。
// 关键断言全部围绕终态：非终态响应不得产出 result；超时不得返回半截结果；
// 缺指标不得补 0。
vi.mock("@/lib/auth", () => ({ getAccessToken: () => null }));

const { apiMock, apiPostMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  apiPostMock: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
}));

// useBatchBacktest 在 runBatch 内部 await，渲染 hook 后要手动驱动
import { useBatchBacktest } from "./use-batch-backtest";

const TASK: ScannerTask = {
  id: "tpl:BTC/USDT:1h",
  templateId: "tpl",
  templateName: "Template",
  symbol: "BTC/USDT",
  interval: "1h",
  params: {},
  status: "pending",
  progress: null,
  lastProgressAt: null,
};

function run(overrides: Parameters<typeof useBatchBacktest>[0] = {}) {
  return renderHook(() =>
    useBatchBacktest({ pollIntervalMs: 1000, maxWaitMs: 60_000, ...overrides }),
  );
}

beforeEach(() => {
  apiMock.mockReset();
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ task_id: "bt-1" });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useBatchBacktest 轮询到终态", () => {
  it("后端连续返回 running 时不产出 result，且不把任务标成 completed", async () => {
    // 这是 A3 的核心回归：早先实现 POST 后只 GET 一次，把 running 响应直接映射成结果。
    apiMock.mockResolvedValue({ status: "running", progress: 40 });
    const { result } = run();

    await act(async () => {
      void result.current.runBatch([TASK]);
    });

    // 推进若干轮询周期，让轮询真的发起多次 GET
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }

    expect(apiMock.mock.calls.length).toBeGreaterThan(1);
    expect(result.current.tasks[0].status).not.toBe("completed");
    expect(result.current.tasks[0].result).toBeUndefined();
    expect(result.current.progress.completed).toBe(0);
  });

  it("只有 status=completed 的响应才落成结果，并把小数比率换算成百分数", async () => {
    apiMock.mockResolvedValue({
      status: "completed",
      task_id: "bt-1",
      total_return: 0.12,
      sharpe_ratio: 1.5,
      max_drawdown: 0.08,
      win_rate: 0.55,
      total_trades: 42,
      profit_factor: 1.8,
    });
    const { result } = run();

    await act(async () => {
      await result.current.runBatch([TASK]);
    });

    expect(result.current.tasks[0].status).toBe("completed");
    expect(result.current.tasks[0].result?.totalReturn).toBeCloseTo(12);
    expect(result.current.tasks[0].result?.maxDrawdown).toBeCloseTo(8);
    expect(result.current.tasks[0].result?.winRate).toBeCloseTo(0.55);
    expect(result.current.tasks[0].result?.totalTrades).toBe(42);
  });

  it("终态响应缺指标时留 null，绝不补 0", async () => {
    // completed 但后端没带指标 —— 这在上游出错时会发生。补 0 会渲染成「收益 0.00%」。
    apiMock.mockResolvedValue({ status: "completed", task_id: "bt-1" });
    const { result } = run();

    await act(async () => {
      await result.current.runBatch([TASK]);
    });

    const r = result.current.tasks[0].result;
    expect(result.current.tasks[0].status).toBe("completed");
    expect(r?.totalReturn).toBeNull();
    expect(r?.sharpe).toBeNull();
    expect(r?.maxDrawdown).toBeNull();
    expect(r?.winRate).toBeNull();
    expect(r?.totalTrades).toBeNull();
    expect(r?.profitFactor).toBeNull();
  });

  it("轮询超时抛 PollTimeoutError，不把半截 running 响应当结果", async () => {
    apiMock.mockResolvedValue({ status: "running", progress: 10 });
    const { result } = run({ maxWaitMs: 5_000 });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      void result.current.runBatch([TASK]);
      await vi.advanceTimersByTimeAsync(6_000);
    });

    expect(result.current.tasks[0].status).toBe("failed");
    expect(result.current.tasks[0].error).toMatch(/did not reach a terminal state/);
    expect(result.current.tasks[0].result).toBeUndefined();
  });

  it("超时错误是 PollTimeoutError 实例（可被调用方分辨）", () => {
    const e = new PollTimeoutError("t1", 5_000);
    expect(e).toBeInstanceOf(PollTimeoutError);
    expect(e.taskId).toBe("t1");
  });
});

describe("useBatchBacktest 进度", () => {
  it("progress.percent 读后端 progress，不是已完成任务数/总数", async () => {
    // 一个任务，后端报 40% —— 已完成数是 0，进度条必须显示 40。
    apiMock.mockResolvedValue({ status: "running", progress: 40 });
    const { result } = run();

    await act(async () => {
      void result.current.runBatch([TASK]);
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(result.current.progress.completed).toBe(0);
    expect(result.current.progress.percent).toBe(40);
    expect(result.current.lastProgress).toBe(40);
  });

  it("progress 长时间不变 → 任务标 stalled；进度一动即退回 running", async () => {
    apiMock.mockResolvedValue({ status: "running", progress: 30 });
    const { result } = run({ pollIntervalMs: 1000, stallAfterMs: 5_000 });

    await act(async () => {
      void result.current.runBatch([TASK]);
      await vi.advanceTimersByTimeAsync(8_000);
    });

    expect(result.current.tasks[0].status).toBe("stalled");
    expect(result.current.progress.stalled).toBe(1);

    // 后端重新动起来 → 状态回到 running
    apiMock.mockResolvedValue({ status: "running", progress: 60 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(result.current.tasks[0].status).toBe("running");
    expect(result.current.progress.percent).toBe(60);
  });

  it("lastProgressAt 只在 progress 真变化时刷新，重复报同一进度不算进展", async () => {
    apiMock.mockResolvedValue({ status: "running", progress: 50 });
    const { result } = run({ pollIntervalMs: 1000 });

    await act(async () => {
      void result.current.runBatch([TASK]);
      await vi.advanceTimersByTimeAsync(1_000);
    });
    const firstAt = result.current.lastProgressAt;
    expect(firstAt).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(result.current.lastProgressAt).toBe(firstAt);
  });
});

describe("useBatchBacktest 取消", () => {
  it("abort 后不再发起新的 POST，未完成的任务落 cancelled 而非 failed", async () => {
    const postCalls: string[] = [];
    apiPostMock.mockImplementation(async (_path: string, body: { symbol: string }) => {
      postCalls.push(body.symbol);
      return { task_id: `bt-${body.symbol}` };
    });
    apiMock.mockResolvedValue({ status: "running", progress: 10 });

    const tasks: ScannerTask[] = Array.from({ length: 5 }, (_, i) => ({
      ...TASK,
      id: `t${i}:BTC/USDT:1h`,
      symbol: `S${i}`,
    }));

    const { result } = run({ maxConcurrent: 1, pollIntervalMs: 1000 });

    await act(async () => {
      void result.current.runBatch(tasks);
      await vi.advanceTimersByTimeAsync(500);
    });
    const before = postCalls.length;
    expect(before).toBeGreaterThan(0);

    await act(async () => {
      result.current.abort();
      await vi.advanceTimersByTimeAsync(5000);
    });

    // 关键：abort 之后 cursor 不再前进 —— POST 数不再增长
    expect(postCalls.length).toBe(before);
    expect(result.current.tasks.some((t) => t.status === "cancelled")).toBe(true);
    expect(result.current.tasks.every((t) => t.status !== "failed")).toBe(true);
  });
});

describe("generateScannerTasks / getTopResults", () => {
  it("笛卡尔积生成任务，id 用冒号分隔", () => {
    const list = generateScannerTasks(
      [{ id: "a", name: "A", name_zh: "甲" }, { id: "b", name: "B" }],
      ["X", "Y"],
      ["1h", "4h"],
    );
    expect(list).toHaveLength(8);
    expect(list[0].id).toBe("a:X:1h");
    expect(list[0].templateName).toBe("甲");
    expect(list[0].status).toBe("pending");
    expect(list[0].progress).toBeNull();
    expect(list.every((t) => t.lastProgressAt === null)).toBe(true);
  });

  it("排序时缺指标的排在最后，而不是被当成最小值/最大值", () => {
    const base = { taskId: "x", templateId: "t", templateName: "T", symbol: "S", interval: "1h", params: {} };
    const results = [
      { ...base, taskId: "a", sharpe: null, totalReturn: null, maxDrawdown: null, winRate: null, totalTrades: null, profitFactor: null },
      { ...base, taskId: "b", sharpe: 0.5, totalReturn: 1, maxDrawdown: 1, winRate: 0.1, totalTrades: 1, profitFactor: 1 },
      { ...base, taskId: "c", sharpe: 2.0, totalReturn: 2, maxDrawdown: 2, winRate: 0.2, totalTrades: 2, profitFactor: 2 },
    ];
    expect(getTopResults(results, "sharpe").map((r) => r.taskId)).toEqual(["c", "b", "a"]);
  });
});
