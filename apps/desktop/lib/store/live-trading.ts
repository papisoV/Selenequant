"use client";

import { create } from "zustand";
import type {
  TradingOrder,
  TradingPosition,
  TradingBalance,
  TradingFill,
  LiveTradingWSMessage,
} from "@/lib/types/live-trading";
import type { StreamState } from "@/lib/ws/use-markets-ws";

// LiveTradingState — 实盘账户实时状态
// 来源：旧代码 apps/desktop/lib/use-trading-ws.ts:34-49，升级为 zustand slice
// 消费者：trd-page（按 mode 切换 paper/live 时使用）
// 写入：useTradingWs hook（顶层 mount 一次，写入 store，消费组件用 selector 订阅）

interface LiveTradingState {
  connected: boolean;
  streamState: StreamState;
  orders: TradingOrder[];
  positions: TradingPosition[];
  balances: TradingBalance[];
  fills: TradingFill[];
  /** Increments on each state-changing WS message so consumers can react. */
  version: number;
  // actions
  reset: () => void;
  setFromMessage: (msg: LiveTradingWSMessage) => void;
  setStreamState: (s: StreamState) => void;
  setConnected: (c: boolean) => void;
}

export const useLiveTradingStore = create<LiveTradingState>((set) => ({
  connected: false,
  streamState: "recovering",
  orders: [],
  positions: [],
  balances: [],
  fills: [],
  version: 0,

  reset: () =>
    set({
      connected: false,
      streamState: "recovering",
      orders: [],
      positions: [],
      balances: [],
      fills: [],
      version: 0,
    }),

  setStreamState: (s) => set({ streamState: s }),
  setConnected: (c) => set({ connected: c }),

  setFromMessage: (msg) => {
    if (msg.type === "ping" || msg.type === "subscribed" || msg.type === "unsubscribed") return;

    switch (msg.type) {
      case "fill": {
        const fill = (msg.data ?? {}) as unknown as TradingFill;
        set((s) => ({ fills: [fill, ...s.fills].slice(0, 200), version: s.version + 1 }));
        break;
      }
      case "order_update": {
        const order = (msg.data ?? {}) as unknown as TradingOrder;
        set((s) => {
          const idx = s.orders.findIndex((o) => o.id === order.id);
          const next = [...s.orders];
          if (idx >= 0) next[idx] = order;
          else next.unshift(order);
          return { orders: next, version: s.version + 1 };
        });
        break;
      }
      case "position_update": {
        const pos = (msg.data ?? {}) as unknown as TradingPosition;
        set((s) => {
          const idx = s.positions.findIndex((p) => p.symbol === pos.symbol);
          const next = [...s.positions];
          if (idx >= 0) next[idx] = pos;
          else next.push(pos);
          // 清掉 quantity=0 的平仓仓位（旧代码 use-trading-ws.ts:130 逻辑）
          return {
            positions: next.filter(
              (p) => Number(p.quantity ?? p.quantity_base ?? 0) > 0,
            ),
            version: s.version + 1,
          };
        });
        break;
      }
      case "balance_update": {
        const data = msg.data;
        const balances = Array.isArray(data)
          ? (data as unknown as TradingBalance[])
          : [(data ?? {}) as unknown as TradingBalance];
        set((s) => ({ balances, version: s.version + 1 }));
        break;
      }
      default:
        break;
    }
  },
}));
