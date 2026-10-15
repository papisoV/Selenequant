"use client";

import { create } from "zustand";

// TradingContext — 交易域状态（layout/tick/gridParams）
// 拆分自原 context.ts 的 layoutPreset + tick + gridParams 字段
// 消费者：kf-page (layoutPreset) + vhero tick + grid-page (gridParams)

export type LayoutPreset =
  | "default"
  | "compact"
  | "focus"
  | "grid2"
  | "grid4"
  | "charts6"
  | "charts9"
  | "minimal"
  | "pro";

export interface TickState {
  equity: number;
  pnlToday: number;
  flash: "equity" | "pnl" | null;
}

interface TradingState {
  gridParams: {
    upper: number;
    lower: number;
    levels: number;
    size: number;
  };
  layoutPreset: LayoutPreset;
  tick: TickState;
  setGridParams: (params: Partial<TradingState["gridParams"]>) => void;
  setLayoutPreset: (preset: LayoutPreset) => void;
  setTick: (tick: Partial<TickState>) => void;
}

export const useTradingStore = create<TradingState>((set) => ({
  gridParams: {
    upper: 70000,
    lower: 64000,
    levels: 10,
    size: 0.1,
  },
  layoutPreset: "default",
  tick: {
    equity: 0,
    pnlToday: 0,
    flash: null,
  },
  setGridParams: (params) =>
    set((s) => ({ gridParams: { ...s.gridParams, ...params } })),
  setLayoutPreset: (preset) => set({ layoutPreset: preset }),
  setTick: (t) =>
    set((s) => ({ tick: { ...s.tick, ...t } })),
}));
