"use client";

import { create } from "zustand";
import type {
  PaperAccountData,
  PaperPositionData,
  PaperOrderData,
  PaperFillData,
  PaperEquityPoint,
  StrategySignal,
  RunnerSlotStatus,
  PaperWSMessage,
} from "@/lib/types/paper";
import type { StreamState } from "@/lib/ws/use-markets-ws";

// PaperContext — paper 账户实时状态
// 来源：旧代码 use-paper-ws.ts:40-51，升级为 zustand slice
// 消费者：pos-page / trd-page / tof-page
// 写入：usePaperWs hook（顶层 mount 一次，写入 store，消费组件用 selector 订阅）

interface PaperState {
  connected: boolean;
  streamState: StreamState;
  account: PaperAccountData | null;
  positions: PaperPositionData[];
  orders: PaperOrderData[];
  fills: PaperFillData[];
  equityPoints: PaperEquityPoint[];
  signals: StrategySignal[];
  slotStatus: RunnerSlotStatus | null;
  /** Increments on each state-changing WS message so consumers can react. */
  version: number;
  // actions
  reset: () => void;
  setFromMessage: (msg: PaperWSMessage) => void;
  setStreamState: (s: StreamState) => void;
  setConnected: (c: boolean) => void;
  setEquityHistory: (points: PaperEquityPoint[]) => void;
  clearSignals: () => void;
}

export const usePaperStore = create<PaperState>((set) => ({
  connected: false,
  streamState: "recovering",
  account: null,
  positions: [],
  orders: [],
  fills: [],
  equityPoints: [],
  signals: [],
  slotStatus: null,
  version: 0,

  reset: () =>
    set({
      connected: false,
      streamState: "recovering",
      account: null,
      positions: [],
      orders: [],
      fills: [],
      equityPoints: [],
      signals: [],
      slotStatus: null,
      version: 0,
    }),

  setStreamState: (s) => set({ streamState: s }),
  setConnected: (c) => set({ connected: c }),

  setFromMessage: (msg) => {
    if (msg.type === "ping" || msg.type === "subscribed" || msg.type === "unsubscribed") return;

    switch (msg.type) {
      case "fill": {
        const fill = (msg.data ?? {}) as unknown as PaperFillData;
        set((s) => ({ fills: [fill, ...s.fills].slice(0, 200), version: s.version + 1 }));
        break;
      }
      case "order_update": {
        const order = (msg.data ?? {}) as unknown as PaperOrderData;
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
        const pos = (msg.data ?? {}) as unknown as PaperPositionData;
        set((s) => {
          const idx = s.positions.findIndex(
            (p) => p.symbol === pos.symbol && (p.pos_side ?? p.side) === (pos.pos_side ?? pos.side),
          );
          const next = [...s.positions];
          if (idx >= 0) next[idx] = pos;
          else next.push(pos);
          return { positions: next, version: s.version + 1 };
        });
        break;
      }
      case "balance_update": {
        set((s) => ({ version: s.version + 1 }));
        break;
      }
      case "account_snapshot": {
        const snap = (msg.data ?? {}) as unknown as PaperAccountData & { positions?: PaperPositionData[] };
        set((s) => {
          const result: Partial<PaperState> = { account: snap };
          if (snap.positions) result.positions = snap.positions;
          result.version = s.version + 1;
          return result;
        });
        break;
      }
      case "equity_point": {
        const pt = (msg.data ?? {}) as unknown as PaperEquityPoint;
        set((s) => ({
          equityPoints: [...s.equityPoints, pt].slice(-200),
          version: s.version + 1,
        }));
        break;
      }
      case "runner_status": {
        const d = (msg.data ?? {}) as Record<string, unknown>;
        const slot: RunnerSlotStatus = {
          deployment_id: String(d.deployment_id ?? ""),
          strategy_id: String(d.strategy_id ?? ""),
          symbol: String(d.symbol ?? ""),
          interval: String(d.interval ?? ""),
          position: String(d.position ?? "flat"),
          bar_count: Number(d.bar_count ?? 0),
          signals_emitted: Number(d.signals_emitted ?? 0),
          orders_placed: Number(d.orders_placed ?? 0),
          errors: Number(d.errors ?? 0),
          last_signal_ts: Number(d.last_signal_ts ?? 0),
        };
        set((s) => ({ slotStatus: slot, version: s.version + 1 }));
        break;
      }
      case "strategy_signal": {
        const data = (msg.data ?? {}) as Record<string, unknown>;
        const signalRaw = data.signal;
        const sig: StrategySignal = {
          ts: typeof msg.timestamp === "number" ? msg.timestamp : Number(msg.timestamp) || Date.now(),
          deployment_id: String(data.deployment_id ?? ""),
          strategy_id: String(data.strategy_id ?? ""),
          signal:
            typeof signalRaw === "object" && signalRaw !== null
              ? (signalRaw as {
                  side: string;
                  reason: string;
                  strength?: number | null;
                  price?: number;
                  symbol?: string;
                })
              : String(signalRaw ?? ""),
          order_id: String(data.order_id ?? ""),
          price:
            typeof signalRaw === "object" && signalRaw !== null
              ? (signalRaw as { price?: number }).price
              : undefined,
          symbol:
            typeof signalRaw === "object" && signalRaw !== null
              ? (signalRaw as { symbol?: string }).symbol
              : undefined,
        };
        set((s) => ({ signals: [sig, ...s.signals].slice(0, 100), version: s.version + 1 }));
        break;
      }
      default:
        break;
    }
  },

  setEquityHistory: (points) => set((s) => ({ equityPoints: points, version: s.version + 1 })),
  clearSignals: () => set((s) => ({ signals: [], version: s.version + 1 })),
}));
