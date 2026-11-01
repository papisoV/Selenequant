"use client";

import { create } from "zustand";

// AccountGateStore — 按 contextId 隔离的授权模式映射(空态/demo/live)
// 业务主线"未授权 → 引导授权 → 解锁功能"漏斗的前端壳:多 context 各自独立状态
// 消费者:AccountGate(components/shared/account-gate.tsx)

export type GateMode = "empty" | "demo" | "live";

interface AccountGateState {
  modes: Record<string, GateMode>;
  getMode: (contextId: string) => GateMode;
  setMode: (contextId: string, mode: GateMode) => void;
  reset: (contextId: string) => void;
}

export const useAccountGateStore = create<AccountGateState>((set, get) => ({
  modes: {},
  getMode: (contextId) => get().modes[contextId] ?? "empty",
  setMode: (contextId, mode) =>
    set((s) => ({ modes: { ...s.modes, [contextId]: mode } })),
  reset: (contextId) =>
    set((s) => {
      if (!(contextId in s.modes)) return s;
      const modes = { ...s.modes };
      delete modes[contextId];
      return { modes };
    }),
}));
