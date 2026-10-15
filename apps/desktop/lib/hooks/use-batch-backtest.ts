"use client";

// 批量回测扫描状态机
// 参考 旧代码/apps/desktop/lib/hooks/use-batch-backtest.ts（只学逻辑不搬视觉）
// 适配新前端 apiClient（apiPost / api 而非 runBacktest / pollBacktestResult）
//
// 职责：
// - 生成 ScannerTask[]（template × symbol × interval 笛卡尔积）
// - 并发执行批量回测（maxConcurrent 限流 + AbortController 取消）
// - 每个 task: POST /api/v1/backtests 启动 → GET /api/v1/backtests/{task_id} 轮询
// - 返回 { tasks, runBatch, clearTasks, isLoading, progress, abort }

import { useCallback, useRef, useState } from "react";
import { api, apiPost } from "@/lib/api-client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ScannerTask {
  id: string;
  templateId: string;
  templateName: string;
  symbol: string;
  interval: string;
  params: Record<string, unknown>;
  status: "pending" | "running" | "completed" | "failed";
  result?: ScannerResult;
  error?: string;
}

export interface ScannerResult {
  taskId: string;
  templateId: string;
  templateName: string;
  symbol: string;
  interval: string;
  params: Record<string, unknown>;
  totalReturn: number;
  sharpe: number;
  maxDrawdown: number;
  winRate: number;
  totalTrades: number;
  profitFactor: number;
  sortino?: number;
  calmar?: number;
}

export interface ScannerProgress {
  total: number;
  completed: number;
  failed: number;
  running: number;
  pending: number;
}

export interface UseBatchBacktestOptions {
  maxConcurrent?: number;
  pollIntervalMs?: number;
  maxWaitMs?: number;
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
  total_return?: number;
  sharpe_ratio?: number;
  max_drawdown?: number;
  win_rate?: number;
  profit_factor?: number;
  total_trades?: number;
  calmar_ratio?: number;
  sortino_ratio?: number;
}

// 轮询单个回测任务直到完成或超时
async function pollBacktestResult(
  taskId: string,
  maxWaitMs: number,
  pollIntervalMs: number,
  signal: AbortSignal,
): Promise<BacktestResultResponse> {
  const deadline = Date.now() + maxWaitMs;
  let lastResult: BacktestResultResponse = {};

  while (Date.now() < deadline) {
    if (signal.aborted) throw new Error("Aborted");
    try {
      const r = await api<BacktestResultResponse>(`/api/v1/backtests/${taskId}`);
      lastResult = r;
      if (r.status === "completed") return r;
      if (r.status === "failed") throw new Error("Backtest failed");
    } catch (e) {
      // 轮询期间网络瞬断 → 继续重试直到 deadline
      if (e instanceof Error && (e.message === "Aborted" || e.message === "Backtest failed")) {
        throw e;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  // 超时返回最后一次拿到的结果（可能 status 还在 running）
  return lastResult;
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

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useBatchBacktest(options: UseBatchBacktestOptions = {}) {
  const {
    maxConcurrent = 5,
    pollIntervalMs = 2000,
    maxWaitMs = 60_000,
  } = options;

  const [tasks, setTasks] = useState<ScannerTask[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const progress: ScannerProgress = {
    total: tasks.length,
    completed: tasks.filter((t) => t.status === "completed").length,
    failed: tasks.filter((t) => t.status === "failed").length,
    running: tasks.filter((t) => t.status === "running").length,
    pending: tasks.filter((t) => t.status === "pending").length,
  };

  const updateTask = useCallback(
    (id: string, patch: Partial<ScannerTask>) => {
      setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
    },
    [],
  );

  const runSingleBacktest = useCallback(
    async (task: ScannerTask, signal: AbortSignal): Promise<ScannerResult> => {
      const runResp = await apiPost<BacktestRunResponse>("/api/v1/backtests", {
        strategy_id: task.templateId,
        symbol: task.symbol,
        interval: task.interval,
        parameters: task.params,
        initial_capital: 10000,
      });
      const taskId = runResp.task_id;
      const r = await pollBacktestResult(taskId, maxWaitMs, pollIntervalMs, signal);
      return {
        taskId: r.task_id ?? r.id ?? taskId,
        templateId: task.templateId,
        templateName: task.templateName,
        symbol: task.symbol,
        interval: task.interval,
        params: task.params,
        totalReturn: (r.total_return ?? 0) * 100,
        sharpe: r.sharpe_ratio ?? 0,
        maxDrawdown: (r.max_drawdown ?? 0) * 100,
        winRate: r.win_rate ?? 0,
        totalTrades: r.total_trades ?? 0,
        profitFactor: r.profit_factor ?? 1,
        sortino: r.sortino_ratio,
        calmar: r.calmar_ratio,
      };
    },
    [maxWaitMs, pollIntervalMs],
  );

  const runBatch = useCallback(
    async (taskList: ScannerTask[]) => {
      if (taskList.length === 0) return;
      // 初始化所有 task 为 pending
      const initial = taskList.map((t) => ({ ...t, status: "pending" as const }));
      setTasks(initial);
      setIsLoading(true);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      await runConcurrent(
        initial,
        maxConcurrent,
        runSingleBacktest,
        controller.signal,
        (item) => updateTask(item.id, { status: "running", error: undefined }),
        (item, result, error) => {
          if (error) {
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
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      setIsLoading(false);
      // 标记所有 running/pending 任务为 failed
      setTasks((prev) =>
        prev.map((t) =>
          t.status === "running" || t.status === "pending"
            ? { ...t, status: "failed" as const, error: "Aborted" }
            : t,
        ),
      );
    }
  }, []);

  const clearTasks = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setTasks([]);
    setIsLoading(false);
  }, []);

  return {
    tasks,
    runBatch,
    clearTasks,
    abort,
    isLoading,
    progress,
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

export function getTopResults(
  results: ScannerResult[],
  metric: "sharpe" | "totalReturn" | "winRate" | "profitFactor" = "sharpe",
  limit = 20,
): ScannerResult[] {
  return [...results].sort((a, b) => b[metric] - a[metric]).slice(0, limit);
}
