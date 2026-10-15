"use client";

import { useRouter } from "next/navigation";
import { apiPost } from "@/lib/api-client";
import type { OptimizationResult } from "@/lib/store/workflow";

/** Tuning 结果摘要 — 与 tuning-results-panel.tsx 内部 interface 等价 */
export interface TuningResultSummary {
  file_name: string;
  timestamp: string;
  strategy_id: string;
  strategy_name: string;
  interval: string;
  exchange: string;
  market_type: string;
  symbol: string;
  best_sharpe: number;
  best_params: Record<string, number>;
  total_return: number;
  max_drawdown: number;
  win_rate: number;
  n_trials: number;
  optimization_time: number;
  total_trades?: number;
  profit_factor?: number;
  oos_sharpe?: number | null;
  oos_rejected?: boolean;
  all_rejected?: boolean;
  deflated_sharpe_ratio?: number | null;
  pre_noop_fix?: boolean;
  annualization_factor?: number | null;
}

/** 从 tuning 结果创建策略任务 → setOptimization → 跳 /trade/backtests
 *  单一入口,P13a 合并自 tuning-results-panel.goToStudio + strat-page.applyTuningResult
 *  错误处理: console.error 带 prefix,不静默吞 */
export async function applyTuningResult(
  result: TuningResultSummary,
  router: ReturnType<typeof useRouter>,
  setOptimization: (r: OptimizationResult) => void,
): Promise<void> {
  try {
    const data = await apiPost<{ id: string }>(
      `/api/v1/strategies/factory-templates/${result.strategy_id}/create`,
      {},
    );
    const taskId = data?.id;
    setOptimization({
      source: "tuning",
      taskId,
      sharpe: result.best_sharpe,
      params: result.best_params || {},
      interval: result.interval,
      symbol: result.symbol || "BTC/USDT",
      pickedAt: Date.now(),
    });
    router.push("/trade/backtests");
  } catch (err) {
    console.error("[tuning-bridge] applyTuningResult failed:", err);
  }
}
