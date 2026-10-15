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
}

export async function runBacktest(body: RunBacktestBody): Promise<{ task_id?: string; status?: string }> {
  return await apiPost(`${BASE}`, body);
}
