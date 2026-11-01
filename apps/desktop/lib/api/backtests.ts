"use client";

import { api, apiDelete, apiPost } from "@/lib/api-client";

const BASE = "/api/v1/backtests";

export interface BacktestTrade {
  entry_time?: number;
  exit_time?: number;
  side?: string;
  entry_price?: number;
  exit_price?: number;
  quantity?: number;
  pnl?: number;
  symbol?: string;
}

export interface BacktestDetailConfigSnapshot {
  initial_capital?: number;
  commission_rate?: number;
  slippage_bps?: number;
  fill_rule?: string;
  exchange?: string;
  market_type?: string;
  [k: string]: unknown;
}

/** One resampling check. Any member may instead carry an `error` string when
 *  it refused (too few trades, bad input) — that is a refusal, not a pass. */
export interface BacktestRobustnessCheck {
  error?: string;
  [k: string]: unknown;
}

export interface BacktestRobustnessPermutation extends BacktestRobustnessCheck {
  actual_sharpe?: number;
  actual_max_dd?: number;
  p_value_sharpe?: number;
  p_value_max_dd?: number;
  simulated_sharpe_mean?: number;
  simulated_sharpe_p5?: number;
  simulated_sharpe_p95?: number;
  n_trades?: number;
  n_simulations?: number;
  p_value_floor?: number;
}

export interface BacktestRobustnessBootstrap extends BacktestRobustnessCheck {
  observed_sharpe?: number;
  ci_lower?: number;
  ci_upper?: number;
  median_sharpe?: number;
  prob_positive?: number;
  confidence?: number;
}

export interface BacktestRobustness {
  error?: string;
  permutation?: BacktestRobustnessPermutation;
  bootstrap?: BacktestRobustnessBootstrap;
  equity_bands?: BacktestRobustnessCheck;
  seed?: number;
}

export interface BacktestDetail {
  id?: string;
  task_id?: string;
  strategy_id?: string;
  strategy_name?: string;
  symbol?: string;
  interval?: string;
  start_date?: string;
  end_date?: string;
  status?: string;
  created_at?: number;
  total_return?: number;
  annual_return?: number;
  sharpe_ratio?: number;
  max_drawdown?: number;
  win_rate?: number;
  profit_factor?: number;
  total_trades?: number;
  calmar_ratio?: number;
  trades?: BacktestTrade[];
  equity_curve?: number[];
  drawdown_curve?: number[];
  buy_hold_curve?: number[];
  exposure_curve?: number[];
  timestamps?: number[];
  lookahead_warnings?: string[];
  config_snapshot?: BacktestDetailConfigSnapshot;
  robustness?: BacktestRobustness;
  result?: Record<string, unknown>;
  [k: string]: unknown;
}

export async function getBacktestDetail(taskId: string): Promise<BacktestDetail> {
  return await api<BacktestDetail>(`${BASE}/${encodeURIComponent(taskId)}`);
}

/** 删除一条回测(内存任务/结果 + 两张持久化表)。返回被删的 id。 */
export async function deleteBacktest(taskId: string): Promise<{ deleted?: string }> {
  return await apiDelete<{ deleted?: string }>(`${BASE}/${encodeURIComponent(taskId)}`);
}

export interface FactoryTemplate {
  id: string;
  name: string;
  category?: string;
  description?: string;
  [k: string]: unknown;
}

export function listFactoryTemplates(): Promise<FactoryTemplate[]> {
  return api<FactoryTemplate[]>(`/api/v1/strategies/factory-templates`);
}

export interface RunBacktestBody {
  strategy_id: string;
  symbol?: string;
  interval?: string;
  exchange?: string;
  market_type?: string;
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  commission_rate?: number;
  slippage_bps?: number;
  /** Server default is true; send false to skip the resampling checks. */
  robustness?: boolean;
}

export async function runBacktest(body: RunBacktestBody): Promise<{ task_id?: string; status?: string }> {
  return await apiPost(`${BASE}`, body);
}
