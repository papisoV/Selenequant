// Live trading WS message types
// 来源：旧代码 apps/desktop/lib/use-trading-ws.ts + 旧代码 apps/desktop/lib/api-client.ts
// 新前端放在 lib/types/live-trading.ts，供 store/live-trading.ts + ws/use-trading-ws.ts 共用
// 注意：与 paper trading 区分——这里是实盘账户的订单/持仓/余额/成交

export interface TradingOrder {
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

export interface TradingPosition {
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

export interface TradingBalance {
  currency?: string;
  total?: number;
  available?: number;
  used?: number;
  equity?: number;
  pnl?: number;
}

export interface TradingFill {
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

// WS 消息 envelope
// 来源：services/local-api/app/api/v1/ws.py 的 ws_trading handler
// 消息类型：order_update / fill / position_update / balance_update / ping / subscribed
export interface LiveTradingWSMessage {
  type:
    | "ping"
    | "subscribed"
    | "unsubscribed"
    | "order_update"
    | "fill"
    | "position_update"
    | "balance_update";
  data?: Record<string, unknown>;
  timestamp?: number | string;
  seq?: number;
}
