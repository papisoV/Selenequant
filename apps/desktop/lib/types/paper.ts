// Paper trading WS message types
// 来源：旧代码 apps/desktop/lib/use-paper-ws.ts:16-50
// 新前端放在 lib/types/paper.ts，供 store/paper.ts + ws/use-paper-ws.ts 共用

export interface PaperAccountData {
  account_id?: string;
  balance?: number;
  equity?: number;
  margin?: number;
  available?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  currency?: string;
  leverage?: number;
  positions?: PaperPositionData[];
}

export interface PaperPositionData {
  symbol: string;
  side?: string;
  pos_side?: string;
  quantity?: number;
  quantity_base?: number;
  avg_entry_price?: number;
  current_price?: number;
  unrealized_pnl?: number;
  leverage?: number;
  margin?: number;
  liquidation_price?: number;
}

export interface PaperOrderData {
  id: string;
  order_id?: string;
  symbol?: string;
  side?: string;
  type?: string;
  price?: number | null;
  quantity?: number;
  quantity_base?: number;
  filled_quantity?: number;
  avg_fill_price?: number | null;
  status?: string;
  created_at?: number;
  updated_at?: number;
}

export interface PaperFillData {
  fill_id?: string;
  id?: string;
  order_id?: string;
  symbol?: string;
  side?: string;
  price?: number;
  quantity?: number;
  quantity_base?: number;
  fee?: number;
  realized_pnl?: number;
  timestamp?: number;
  ts?: number;
}

export interface PaperEquityPoint {
  ts: number;
  equity: number;
  balance?: number;
  unrealized_pnl?: number;
}

export interface StrategySignal {
  ts: number;
  deployment_id: string;
  strategy_id: string;
  signal: { side: string; reason: string; strength?: number | null; price?: number; symbol?: string } | string;
  order_id: string;
  price?: number;
  symbol?: string;
}

export interface RunnerSlotStatus {
  deployment_id: string;
  strategy_id: string;
  symbol: string;
  interval: string;
  position: string;
  bar_count: number;
  signals_emitted: number;
  orders_placed: number;
  errors: number;
  last_signal_ts: number;
}

// WS 消息 envelope
export interface PaperWSMessage {
  type:
    | "ping"
    | "subscribed"
    | "unsubscribed"
    | "fill"
    | "order_update"
    | "position_update"
    | "balance_update"
    | "account_snapshot"
    | "equity_point"
    | "runner_status"
    | "strategy_signal";
  data?: Record<string, unknown>;
  account_id?: string;
  timestamp?: number | string;
  seq?: number;
}
