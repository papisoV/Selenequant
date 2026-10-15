"use client"

import type { APIResponse } from "@/lib/types"

// Strategy domain types for the strategy components
// These bridge the old api-client types with the new frontend's type system

export interface StrategyData {
  id: string
  name: string
  type: string
  description?: string
  symbols?: string[]
  symbol?: string
  interval?: string
  direction?: "long_only" | "short_only" | "neutral"
  parameters?: Record<string, unknown>
  entry_rules?: Record<string, unknown>
  exit_rules?: Record<string, unknown>
  risk_params?: Record<string, unknown>
  tags?: string[]
  version?: number
  source?: string
  lifecycle_state?: string
}

export interface BacktestMetrics {
  total_return: number
  annual_return: number
  sharpe_ratio: number
  sortino_ratio: number
  max_drawdown: number
  calmar_ratio: number
  win_rate: number
  profit_factor: number
  total_trades: number
  volatility?: number
  var_95?: number
  cvar_95?: number
}

export interface BacktestTradeData {
  entry_time: number
  exit_time: number
  side: string
  entry_price: number
  exit_price: number
  quantity: number
  pnl: number
  symbol?: string
  entry_action?: string
  exit_action?: string
}

export interface BacktestResult {
  metrics?: BacktestMetrics
  equity_curve?: number[]
  drawdown_curve?: number[]
  buy_hold_curve?: number[]
  trades?: BacktestTradeData[]
  timestamps?: number[]
  start_date?: string
  end_date?: string
}

export interface BacktestData {
  id?: string
  task_id?: string
  strategy_name?: string
  symbol?: string
  interval?: string
  start_date?: string
  end_date?: string
  total_return?: number
  annual_return?: number
  sharpe_ratio?: number
  max_drawdown?: number
  win_rate?: number
  profit_factor?: number
  total_trades?: number
  calmar_ratio?: number
  trades?: BacktestTradeData[]
  equity_curve?: number[]
  drawdown_curve?: number[]
  buy_hold_curve?: number[]
  timestamps?: number[]
  result?: BacktestResult
  custom_metrics?: CustomMetricsData
}

export interface CustomMetricsData {
  regime?: string
  hurst?: number
  zscore?: number
  half_life?: number
  obv?: number
  cmf?: number
  mfi?: number
  force_index?: number
}

export interface KlineData {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface StrategyDeploymentData {
  id: string
  strategy_id: string
  account_id?: string
  status?: string
  strategy_version?: number
  created_at?: string
}

export interface PaperEquityPoint {
  equity: number
  timestamp: number
}

export interface PaperAccountData {
  equity: number
  initial_balance: number
}

export interface ChatSession {
  id: string
  title?: string
  created_at?: number
}

export interface AgentChatContext {
  intent: string
  strategy_id: string
  strategy_name: string
  symbol?: string
  timeframe?: string
}

export interface AgentChatResult {
  sessionId?: string
}
