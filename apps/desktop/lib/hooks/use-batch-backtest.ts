"use client";

// 批量回测扫描状态机
//
// 职责：
// - 生成 ScannerTask[]（template × symbol × interval 笛卡尔积）
// - 并发执行批量回测（maxConcurrent 限流 + AbortController 取消）
// - 每个 task: POST /api/v1/backtests 启动 → GET /api/v1/backtests/{task_id} 轮询到终态
// - 只把 status=completed 的响应映射成结果；缺指标留 null，绝不补 0
//
// 「缺指标留 null」是硬约束，不是风格选择：后端在非终态响应里根本不带指标字段
// （backtests.py `_task_to_frontend_dict`：只有非终态才加 progress，指标随 result
// 一起出现）。把缺失补成 0 会把「还没算完」渲染成「收益 0.00%」，再被 >=0 判成绿色。

import { useCallback, useRef, useState } from "react";
import { api, apiPost } from "@/lib/api-client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** stalled = 仍在轮询、但后端 progress 已连续 stallAfterMs 没动。恢复前进会退回 running。 */
export type ScannerTaskStatus =
  | "pending"
  | "running"
  | "stalled"
  | "completed"
  | "failed"
  | "cancelled";

export interface ScannerTask {
  id: string;
  templateId: string;
  templateName: string;
  symbol: string;
  interval: string;
  params: Record<string, unknown>;
  status: ScannerTaskStatus;
  /** 后端 progress(0-100)。未开始与终态为 null —— 后端终态时刻意不返回该字段 */
  progress: number | null;
  /** 上一次 progress 真正变化的时刻(ms)。停滞判定以此为准，轮询本身不刷新它 */
  lastProgressAt: number | null;
  result?: ScannerResult;
  error?: string;
}

/**
 * 指标一律 `number | null`：null 的含义是「这次响应没给」，不是「值为 0」。
 * 消费方必须显式处理 null（渲染 `—`），不能 `?? 0`。
 */
export interface ScannerResult {
  taskId: string;
  templateId: string;
  templateName: string;
  symbol: string;
  interval: string;
  params: Record<string, unknown>;
  totalReturn: number | null;
  sharpe: number | null;
  maxDrawdown: number | null;
  winRate: number | null;
  totalTrades: number | null;
  profitFactor: number | null;
  sortino?: number;
  calmar?: number;
}

export interface ScannerProgress {
  total: number;
  completed: number;
  failed: number;
  /** 正在轮询且进度在动 */
  running: number;
  /** 正在轮询但进度停滞（仍计入未完成，不计入 running） */
  stalled: number;
  pending: number;
  /** 用户点了「停止」而未跑完 */
  cancelled: number;
  /** 由后端 progress 聚合出的百分比（终态任务计 100），不是「已完成任务数 / 总数」 */
  percent: number;
}

export interface UseBatchBacktestOptions {
  maxConcurrent?: number;
  pollIntervalMs?: number;
  /** 单个任务轮询上限；到点抛 PollTimeoutError，绝不返回半截结果 */
  maxWaitMs?: number;
  /** progress 连续多久不动就标 stalled */
  stallAfterMs?: number;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class AbortedError extends Error {
  constructor() {
    super("Aborted");
    this.name = "AbortedError";
  }
}

export class PollTimeoutError extends Error {
  readonly taskId: string;
  readonly waitedMs: number;
  constructor(taskId: string, waitedMs: number) {
    super(
      `Backtest ${taskId} did not reach a terminal state within ${Math.round(waitedMs / 1000)}s`,
    );
    this.name = "PollTimeoutError";
    this.taskId = taskId;
    this.waitedMs = waitedMs;
  }
}

class BacktestFailedError extends Error {
  constructor(detail?: string) {
    super(detail ? `Backtest failed: ${detail}` : "Backtest failed");
    this.name = "BacktestFailedError";
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateTaskId(templateId: string, symbol: string, interval: string): string {
  return `${templateId}:${symbol}:${interval}`;
}

interface BacktestRunResponse {
  task_id: string;
}

interface BacktestResultResponse {
  id?: string;
  task_id?: string;
  status?: string;
  progress?: number;
  error?: string | null;
  total_return?: number;
  sharpe_ratio?: number;
  max_drawdown?: number;
  win_rate?: number;
  profit_factor?: number;
  total_trades?: number;
  calmar_ratio?: number;
  sortino_ratio?: number;
}

export interface PollTick {
  /** 后端这次报的进度；响应里没有就是 null */
  progress: number | null;
  at: number;
}

/**
 * 轮询单个回测任务直到终态。
 *
 * 只有 `status === "completed"` / `"failed"` 是终态；到 maxWaitMs 仍未终态就抛
 * PollTimeoutError —— 早先返回最后一次响应（往往是 running）会让调用方把它当结果，
 * 那正是扫描页伪造零收益的来源。
 */
async function pollBacktestResult(
  taskId: string,
  maxWaitMs: number,
  pollIntervalMs: number,
  signal: AbortSignal,
  onTick?: (tick: PollTick) => void,
): Promise<BacktestResultResponse> {
  const deadline = Date.now() + maxWaitMs;

  while (Date.now() < deadline) {
    if (signal.aborted) throw new AbortedError();
    try {
      const r = await api<BacktestResultResponse>(`/api/v1/backtests/${taskId}`);
      if (r.status === "completed") return r;
      if (r.status === "failed") throw new BacktestFailedError(r.error ?? undefined);
      onTick?.({
        progress: typeof r.progress === "number" ? r.progress : null,
        at: Date.now(),
      });
    } catch (e) {
      if (e instanceof AbortedError || e instanceof BacktestFailedError) throw e;
      // 轮询期间网络瞬断 → 继续重试直到 deadline
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new PollTimeoutError(taskId, maxWaitMs);
}

// 并发执行器：限制 maxConcurrent，支持 abort
async function runConcurrent<T, R>(
  items: T[],
  maxConcurrent: number,
  executor: (item: T, signal: AbortSignal) => Promise<R>,
  signal: AbortSignal,
  onItemStart: (item: T) => void,
  onItemDone: (item: T, result: R | null, error: Error | null) => void,
): Promise<void> {
  let cursor = 0;
  const workers: Promise<void>[] = [];

  const worker = async () => {
    while (cursor < items.length) {
      if (signal.aborted) return;
      const idx = cursor++;
      const item = items[idx];
      onItemStart(item);
      try {
        const result = await executor(item, signal);
        onItemDone(item, result, null);
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        onItemDone(item, null, err);
      }
    }
  };

  for (let i = 0; i < Math.min(maxConcurrent, items.length); i++) {
    workers.push(worker());
  }
  await Promise.all(workers);
}

function finiteOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 后端比率是小数（0.12 → 12%），前端展示一律百分数 */
function pctOrNull(v: unknown): number | null {
  const n = finiteOrNull(v);
  return n === null ? null : n * 100;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useBatchBacktest(options: UseBatchBacktestOptions = {}) {
  const {
    maxConcurrent = 5,
    pollIntervalMs = 2000,
    maxWaitMs = 600_000,
    stallAfterMs = 120_000,
  } = options;

  const [tasks, setTasks] = useState<ScannerTask[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [lastProgress, setLastProgress] = useState<{ value: number; at: number } | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  // taskId → 上一次看到的 progress，用来判断「真的动了」而不是「又报了一次同样的数」
  const progressRef = useRef<Map<string, number>>(new Map());

  const progress: ScannerProgress = (() => {
    const count = (s: ScannerTaskStatus) => tasks.filter((t) => t.status === s).length;
    const sum = tasks.reduce((acc, t) => {
      if (t.status === "completed" || t.status === "failed") return acc + 100;
      return acc + (t.progress ?? 0);
    }, 0);
    return {
      total: tasks.length,
      completed: count("completed"),
      failed: count("failed"),
      running: count("running"),
      stalled: count("stalled"),
      pending: count("pending"),
      cancelled: count("cancelled"),
      percent: tasks.length > 0 ? Math.round(sum / tasks.length) : 0,
    };
  })();

  const updateTask = useCallback(
    (id: string, patch: Partial<ScannerTask>) => {
      setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
    },
    [],
  );

  const handleTick = useCallback(
    (id: string, tick: PollTick) => {
      const previous = progressRef.current.get(id);
      const advanced = tick.progress !== null && tick.progress !== previous;
      if (advanced) {
        progressRef.current.set(id, tick.progress as number);
        setLastProgress({ value: tick.progress as number, at: tick.at });
      }
      setTasks((prev) =>
        prev.map((t) => {
          if (t.id !== id) return t;
          const lastProgressAt = advanced ? tick.at : t.lastProgressAt;
          const since = tick.at - (lastProgressAt ?? tick.at);
          return {
            ...t,
            progress: tick.progress ?? t.progress,
            lastProgressAt,
            // 停滞只是一次心跳判定：还在轮询，进度一动就退回 running
            status: since >= stallAfterMs ? "stalled" : "running",
          };
        }),
      );
    },
    [stallAfterMs],
  );

  const runSingleBacktest = useCallback(
    async (task: ScannerTask, signal: AbortSignal): Promise<ScannerResult> => {
      const runResp = await apiPost<BacktestRunResponse>(
        "/api/v1/backtests",
        {
          strategy_id: task.templateId,
          symbol: task.symbol,
          interval: task.interval,
          parameters: task.params,
          initial_capital: 10000,
        },
        signal,
      );
      const taskId = runResp.task_id;
      const r = await pollBacktestResult(taskId, maxWaitMs, pollIntervalMs, signal, (tick) =>
        handleTick(task.id, tick),
      );
      return {
        taskId: r.task_id ?? r.id ?? taskId,
        templateId: task.templateId,
        templateName: task.templateName,
        symbol: task.symbol,
        interval: task.interval,
        params: task.params,
        totalReturn: pctOrNull(r.total_return),
        sharpe: finiteOrNull(r.sharpe_ratio),
        maxDrawdown: pctOrNull(r.max_drawdown),
        winRate: finiteOrNull(r.win_rate),
        totalTrades: finiteOrNull(r.total_trades),
        profitFactor: finiteOrNull(r.profit_factor),
        sortino: r.sortino_ratio,
        calmar: r.calmar_ratio,
      };
    },
    [maxWaitMs, pollIntervalMs, handleTick],
  );

  const runBatch = useCallback(
    async (taskList: ScannerTask[]) => {
      if (taskList.length === 0) return;
      const initial: ScannerTask[] = taskList.map((t) => ({
        ...t,
        status: "pending",
        progress: null,
        lastProgressAt: null,
        result: undefined,
        error: undefined,
      }));
      progressRef.current = new Map();
      setTasks(initial);
      setLastProgress(null);
      setIsLoading(true);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      await runConcurrent(
        initial,
        maxConcurrent,
        runSingleBacktest,
        controller.signal,
        (item) =>
          updateTask(item.id, {
            status: "running",
            error: undefined,
            lastProgressAt: Date.now(),
          }),
        (item, result, error) => {
          if (error) {
            // 用户按了停止：是「停下」，不是「失败」，两者不能共用一个计数
            if (error instanceof AbortedError || controller.signal.aborted) {
              updateTask(item.id, { status: "cancelled" });
              return;
            }
            updateTask(item.id, { status: "failed", error: error.message });
          } else if (result) {
            updateTask(item.id, { status: "completed", result });
          }
        },
      );

      setIsLoading(false);
      abortControllerRef.current = null;
    },
    [maxConcurrent, runSingleBacktest, updateTask],
  );

  const abort = useCallback(() => {
    const controller = abortControllerRef.current;
    if (!controller) return;
    controller.abort();
    setIsLoading(false);
    // 在飞的任务会各自收到 AbortedError 并自行落 cancelled；这里先兜住还没开始的
    setTasks((prev) =>
      prev.map((t) =>
        t.status === "pending" || t.status === "running" || t.status === "stalled"
          ? { ...t, status: "cancelled" as const }
          : t,
      ),
    );
  }, []);

  const clearTasks = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    progressRef.current = new Map();
    setTasks([]);
    setLastProgress(null);
    setIsLoading(false);
  }, []);

  return {
    tasks,
    runBatch,
    clearTasks,
    abort,
    isLoading,
    progress,
    lastProgress: lastProgress?.value ?? null,
    lastProgressAt: lastProgress?.at ?? null,
  };
}

// ---------------------------------------------------------------------------
// 工具函数（供 scanner-page 用）
// ---------------------------------------------------------------------------

export interface FactoryTemplate {
  id: string;
  name: string;
  name_zh?: string;
  category?: string;
  default_params?: Record<string, unknown>;
}

// 从 templates × symbols × intervals 生成 ScannerTask[]
export function generateScannerTasks(
  templates: FactoryTemplate[],
  symbols: string[],
  intervals: string[],
): ScannerTask[] {
  const list: ScannerTask[] = [];
  for (const template of templates) {
    for (const symbol of symbols) {
      for (const interval of intervals) {
        list.push({
          id: generateTaskId(template.id, symbol, interval),
          templateId: template.id,
          templateName: template.name_zh ?? template.name,
          symbol,
          interval,
          params: { ...(template.default_params ?? {}) },
          status: "pending",
          progress: null,
          lastProgressAt: null,
        });
      }
    }
  }
  return list;
}

// 收集结果为 templateId → (symbol:interval) → result 的嵌套 Map（O(1) 查询）
export function collectResultsToMap(
  results: ScannerResult[],
): Map<string, Map<string, ScannerResult>> {
  const m = new Map<string, Map<string, ScannerResult>>();
  for (const r of results) {
    if (!m.has(r.templateId)) m.set(r.templateId, new Map());
    m.get(r.templateId)!.set(`${r.symbol}:${r.interval}`, r);
  }
  return m;
}

/**
 * 按指标排序取前 N。缺指标的排最后 —— null 不是「最差」，是「这次没给」，
 * 把它排进榜尾会和真正排在榜尾的结果混在一起。
 */
export function getTopResults(
  results: ScannerResult[],
  metric: "sharpe" | "totalReturn" | "winRate" | "profitFactor" = "sharpe",
  limit = 20,
): ScannerResult[] {
  return [...results]
    .sort((a, b) => {
      const va = a[metric];
      const vb = b[metric];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return vb - va;
    })
    .slice(0, limit);
}
