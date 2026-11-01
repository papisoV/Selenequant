"use client";

import { create } from "zustand";
import type { StrategySignalMessage, StrategyWSMessage } from "@/lib/types/strategy";
import type { StreamState } from "@/lib/ws/use-markets-ws";

// StrategyState — 策略信号实时流
// 来源：旧代码 apps/desktop/lib/use-strategy-ws.ts:22-110，升级为 zustand slice
// 消费者：serenity-page / serenity-panel（策略信号展示）
// 写入：useStrategyWs hook（顶层 mount 一次，写入 store，消费组件用 selector 订阅）

interface StrategyState {
  connected: boolean;
  streamState: StreamState;
  signals: StrategySignalMessage[];
  latestSignal: StrategySignalMessage | null;
  /** Increments on each new signal so consumers can react. */
  version: number;
  // actions
  reset: () => void;
  appendSignal: (sig: StrategySignalMessage) => void;
  setStreamState: (s: StreamState) => void;
  setConnected: (c: boolean) => void;
  setFromMessage: (msg: StrategyWSMessage) => void;
}

export const useStrategyStore = create<StrategyState>((set) => ({
  connected: false,
  streamState: "recovering",
  signals: [],
  latestSignal: null,
  version: 0,

  reset: () =>
    set({
      connected: false,
      streamState: "recovering",
      signals: [],
      latestSignal: null,
      version: 0,
    }),

  setStreamState: (s) => set({ streamState: s }),
  setConnected: (c) => set({ connected: c }),

  appendSignal: (sig) =>
    set((s) => ({
      signals: [sig, ...s.signals].slice(0, 100),
      latestSignal: sig,
      version: s.version + 1,
    })),

  setFromMessage: (msg) => {
    if (msg.type === "ping" || msg.type === "subscribed" || msg.type === "unsubscribed") return;
    if (msg.type === "strategy_signal") {
      const data = (msg.data ?? {}) as Record<string, unknown>;
      const side = String(data.side ?? "").toLowerCase() as "buy" | "sell" | "hold";
      const sig: StrategySignalMessage = {
        strategy_id: String(data.strategy_id ?? ""),
        symbol: String(data.symbol ?? ""),
        side: side === "buy" || side === "sell" ? side : "hold",
        strength: Number(data.strength ?? 0),
        reason: String(data.reason ?? ""),
        timestamp:
          typeof msg.timestamp === "number"
            ? msg.timestamp
            : typeof msg.timestamp === "string"
              ? Number(msg.timestamp) || Date.now()
              : Date.now(),
        price: typeof data.price === "number" ? data.price : undefined,
      };
      set((s) => ({
        signals: [sig, ...s.signals].slice(0, 100),
        latestSignal: sig,
        version: s.version + 1,
      }));
    }
  },
}));
