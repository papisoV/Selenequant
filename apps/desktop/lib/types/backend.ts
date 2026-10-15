// 后端 Pydantic model 对应的 TS type
// 来源：packages/shared-types/selene_space_types/*.py

// ---------------------------------------------------------------------------
// common — APIResponse 包装
// ---------------------------------------------------------------------------

export interface Pagination {
  offset: number;
  limit: number;
  total: number;
}

export interface ErrorInfo {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ResponseMeta {
  request_id?: string;
  pagination?: Pagination | null;
}

export interface APIResponse<T> {
  data: T | null;
  meta: ResponseMeta | null;
  error: ErrorInfo | null;
}

// ---------------------------------------------------------------------------
// trading — Order / Position / Fill / PnL
// ---------------------------------------------------------------------------

export type ExecutionMode =
  | "paper"
  | "shadow"
  | "confirm"
  | "limited_auto"
  | "full_auto"
  | "live";

export type OrderSide = "buy" | "sell";

export type MarginMode = "cross" | "isolated" | "cash";

export type PositionSide = "long" | "short" | "net";

export type OrderType =
  | "market"
  | "limit"
  | "stop_market"
  | "stop_limit"
  | "ioc";

export type OrderStatus =
  | "created"
  | "accepted"
  | "partial"
  | "filled"
  | "cancelled"
  | "rejected"
  | "expired"
  | "pending_cancel"
  | "triggered";

export interface Order {
  id: string;
  symbol: string;
  side: OrderSide;
  type: OrderType;
  status: OrderStatus;
  quantity: number;
  price?: number | null;
  stop_price?: number | null;
  filled_quantity: number;
  avg_fill_price?: number | null;
  leverage: number;
  margin_mode: MarginMode;
  pos_side: PositionSide;
  reduce_only: boolean;
  created_at: number;
  updated_at: number;
}

export interface Fill {
  id: string;
  order_id: string;
  client_order_id?: string;
  price: number;
  quantity: number;
  fee: number;
  fee_currency: string;
  fee_rate: number;
  realized_pnl: number;
  exec_type: string;
  side: string;
  pos_side: string;
  symbol: string;
  leverage: number;
  reduce_only: boolean;
  timestamp: number;
}

export interface Position {
  symbol: string;
  side: OrderSide;
  pos_side: PositionSide;
  quantity: number;
  quantity_base: number;
  avg_entry_price: number;
  leverage: number;
  margin_mode: MarginMode;
  margin: number;
  liquidation_price?: number | null;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  realized_pnl: number;
  current_price?: number | null;
  opened_at: number;
  updated_at: number;
  accrued_funding: number;
  last_funding_time?: number | null;
  borrow_fee_accrued: number;
}

export interface PnLRecord {
  symbol: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  fees: number;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// market — Kline / Ticker / OrderBook
// ---------------------------------------------------------------------------

export interface Kline {
  open_time?: number;
  timestamp?: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  close_time?: number;
}

export interface KlineResponse {
  symbol: string;
  interval: string;
  exchange: string;
  market_type: string;
  klines: Kline[];
  source: string;
}

export interface Ticker {
  symbol: string;
  exchange: string;
  last_price: number;
  bid?: number;
  ask?: number;
  volume_24h?: number;
  quote_volume_24h?: number;
  change_pct_24h?: number;
  timestamp: number;
}

export interface OrderBookLevel {
  price: number;
  quantity: number;
}

export interface OrderBook {
  symbol: string;
  exchange: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  timestamp: number;
}

// ---------------------------------------------------------------------------
// strategy — Strategy / Backtest
// ---------------------------------------------------------------------------

export interface StrategyTemplate {
  id: string;
  name: string;
  category: string;
  description?: string;
  parameters?: Record<string, unknown>;
}

export interface BacktestSummary {
  id: string;
  strategy_id: string;
  symbol: string;
  interval: string;
  start_time: number;
  end_time: number;
  total_return?: number;
  sharpe?: number;
  max_drawdown?: number;
  trades?: number;
  status: string;
}
