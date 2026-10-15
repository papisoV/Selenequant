"use client";

import { apiPost } from "@/lib/api-client";

const BASE = "/api/v1/setup-backtest";

export interface SetupBacktestRequest {
  symbol: string;
  timeframe: string;
  exchange?: string;
  market_type?: string;
  entry: number;
  stop_loss: number;
  take_profit: number;
  direction?: string | null;
  lookback_bars?: number;
  input_id?: string;
}

export interface SetupBacktestTrade {
  setup_index: number;
  entry_timestamp: number;
  exit_timestamp: number;
  outcome: "win" | "loss";
  pnl_r: number;
  bars_held: number;
  entry_price: number;
  stop_loss_price: number;
  take_profit_price: number;
  reward_risk_ratio: number;
  [k: string]: unknown;
}

export interface SetupBacktestBucket {
  count: number;
  wins: number;
  losses: number;
  win_rate: number;
  expectancy: number;
  [k: string]: unknown;
}

export interface SetupBacktestResult {
  total_setups: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  profit_factor: number | null;
  max_drawdown: number;
  avg_bars_held: number;
  rr_buckets: Record<string, SetupBacktestBucket>;
  trades: SetupBacktestTrade[];
  open_setups: number;
  no_trigger: boolean;
  reason: string | null;
  symbol?: string;
  timeframe?: string;
  attached_to_record?: boolean;
  [k: string]: unknown;
}

export async function runSetupBacktest(body: SetupBacktestRequest): Promise<SetupBacktestResult> {
  return await apiPost<SetupBacktestResult>(BASE, body);
}
