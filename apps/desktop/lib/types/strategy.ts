// Strategy signal WS message types
// 来源：旧代码 apps/desktop/lib/use-strategy-ws.ts
// 新前端放在 lib/types/strategy.ts，供 store/strategy.ts + ws/use-strategy-ws.ts 共用
// 注意：与 paper store 里的 StrategySignal 区分（paper 里的带 deployment_id/order_id，是 runner 内部信号）
// 这里是面向用户的策略信号流（strategy_id/symbol/side/strength/reason）
// 为避免与 paper.StrategySignal 名称冲突，导出为 StrategySignalMessage
export interface StrategySignalMessage {
  strategy_id: string;
  symbol: string;
  side: "buy" | "sell" | "hold";
  strength: number;
  reason: string;
  timestamp: number;
  price?: number;
}

// WS 消息 envelope
// 来源：services/local-api/app/api/v1/ws.py 的 ws_strategy handler
// 消息类型：strategy_signal / ping / subscribed
export interface StrategyWSMessage {
  type: "ping" | "subscribed" | "unsubscribed" | "strategy_signal";
  data?: Record<string, unknown>;
  timestamp?: number | string;
  seq?: number;
}
