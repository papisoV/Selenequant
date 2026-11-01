"use client";

import { create } from "zustand";
import { listMiningSessions, type MiningSession } from "@/lib/api/alpha";

// AlphaMiningStore — Alpha 挖矿后台任务的共享可见性状态。
// 消费方:侧边栏运行中徽标(sidebar.tsx)与挖矿面板(alpha-mining-panel.tsx)。
// 单点轮询由 AlphaMiningPoller 驱动(store 保持纯状态,便于测试)。

export const ACTIVE_STATUSES = ["starting", "running", "stopping"] as const;

export function isActive(s: MiningSession): boolean {
  return (ACTIVE_STATUSES as readonly string[]).includes(s.status);
}

interface AlphaMiningState {
  sessions: MiningSession[];
  /** 最近一次 refresh 是否失败(true 时保留旧 sessions,仅标记)。 */
  error: boolean;
  /** 反查某会话是否仍在活跃集合里(挂载期校验持久 id 用)。 */
  hasSession: (id: string) => boolean;
  refresh: () => Promise<void>;
}

export const useAlphaMiningStore = create<AlphaMiningState>((set, get) => ({
  sessions: [],
  error: false,
  hasSession: (id) => get().sessions.some((s) => s.session_id === id && isActive(s)),
  refresh: async () => {
    try {
      const sessions = await listMiningSessions();
      set({ sessions, error: false });
    } catch {
      // 保留旧值:瞬时网络抖动不应让徽标闪烁消失。
      set({ error: true });
    }
  },
}));

/** 活跃会话(用于徽标与面板挂接);派生自 sessions,不单独存状态。 */
export function selectActiveSessions(state: { sessions: MiningSession[] }): MiningSession[] {
  return state.sessions.filter(isActive);
}
