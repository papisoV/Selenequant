"use client";

// ICT 四层叠加层的**显示参数**存档 —— zustand + localStorage。
//
// 为什么与 drawings store 分开:
//   - 这里不含任何坐标/对象,重启应用后应当原样恢复(与磁吸一样是"使用习惯"),
//     而 `activeTool` 这类交互态刻意不落盘;
//   - 独立的 key(见 ict-display.ts)让坏掉一边不拖垮另一边 —— localStorage
//     里躺的是不受信数据,用户手改或旧版本写入都会进 parse 的兜底。
//
// 为什么不用后端:后端没有用户级设置表(查过 migrations_pkg),新建表要动
// 汇聚点;先 localStorage,后续落库时 ict-display.ts 的 parse 就是切点。

import { create } from "zustand";

import {
  ICT_DISPLAY_KEY,
  DEFAULT_ICT_DISPLAY,
  parseIctDisplayJson,
  serializeIctDisplay,
  type IctDealingRangeSettings,
  type IctDisplaySettings,
  type IctEventSettings,
  type IctKillzoneSettings,
  type IctSwingSettings,
} from "@/lib/chart/ict-display";

function persist(next: IctDisplaySettings): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ICT_DISPLAY_KEY, serializeIctDisplay(next));
  } catch {
    // 配额满 / 隐私模式:内存里的设置仍然生效,只是不跨会话。
  }
}

interface IctDisplayStore {
  settings: IctDisplaySettings;
  hydrate: () => void;
  setSwings: (patch: Partial<IctSwingSettings>) => void;
  setSwingTiers: (patch: Partial<IctSwingSettings["tiers"]>) => void;
  setEvents: (patch: Partial<IctEventSettings>) => void;
  setEventKinds: (patch: Partial<IctEventSettings["kinds"]>) => void;
  setKillzone: (patch: Partial<IctKillzoneSettings>) => void;
  setKillzoneSessions: (patch: Partial<IctKillzoneSettings["sessions"]>) => void;
  setDealingRange: (patch: Partial<IctDealingRangeSettings>) => void;
  setDealingRangeLines: (patch: Partial<IctDealingRangeSettings["lines"]>) => void;
  reset: () => void;
}

const clone = (s: IctDisplaySettings): IctDisplaySettings => ({
  swings: { ...s.swings, tiers: { ...s.swings.tiers } },
  events: { ...s.events, kinds: { ...s.events.kinds } },
  killzone: { ...s.killzone, sessions: { ...s.killzone.sessions } },
  dealingRange: { ...s.dealingRange, lines: { ...s.dealingRange.lines } },
});

export const useIctDisplayStore = create<IctDisplayStore>((set, get) => ({
  settings: clone(DEFAULT_ICT_DISPLAY),

  /**
   * 从 localStorage 恢复。只显式调用一次(页面挂载),不在 store 初始化时读:
   * SSR 首帧没有 window,在初始化里读会让服务端/客户端首次渲染分叉。
   */
  hydrate: () => {
    if (typeof window === "undefined") return;
    const parsed = parseIctDisplayJson(window.localStorage.getItem(ICT_DISPLAY_KEY));
    set({ settings: parsed });
  },

  setSwings: (patch) => {
    const cur = get().settings;
    const next = { ...cur, swings: { ...cur.swings, ...patch } };
    set({ settings: next });
    persist(next);
  },
  setSwingTiers: (patch) => {
    const cur = get().settings;
    const next = {
      ...cur,
      swings: { ...cur.swings, tiers: { ...cur.swings.tiers, ...patch } },
    };
    set({ settings: next });
    persist(next);
  },
  setEvents: (patch) => {
    const cur = get().settings;
    const next = { ...cur, events: { ...cur.events, ...patch } };
    set({ settings: next });
    persist(next);
  },
  setEventKinds: (patch) => {
    const cur = get().settings;
    const next = {
      ...cur,
      events: { ...cur.events, kinds: { ...cur.events.kinds, ...patch } },
    };
    set({ settings: next });
    persist(next);
  },
  setKillzone: (patch) => {
    const cur = get().settings;
    const next = { ...cur, killzone: { ...cur.killzone, ...patch } };
    set({ settings: next });
    persist(next);
  },
  setKillzoneSessions: (patch) => {
    const cur = get().settings;
    const merged = { ...cur.killzone.sessions, ...patch };
    // 全关会让后端 400;拒绝这次改动而不是静默落一个"整层消失"的状态。
    if (!merged.london && !merged.new_york && !merged.asia) return;
    const next = { ...cur, killzone: { ...cur.killzone, sessions: merged } };
    set({ settings: next });
    persist(next);
  },
  setDealingRange: (patch) => {
    const cur = get().settings;
    const merged = { ...cur.dealingRange, ...patch };
    // 阈值顺序错了后端会丢掉整层(returns None),这里当场纠正。
    if (merged.discountThreshold > merged.premiumThreshold) {
      const lo = merged.premiumThreshold;
      merged.premiumThreshold = merged.discountThreshold;
      merged.discountThreshold = lo;
    }
    const next = { ...cur, dealingRange: merged };
    set({ settings: next });
    persist(next);
  },
  setDealingRangeLines: (patch) => {
    const cur = get().settings;
    const next = {
      ...cur,
      dealingRange: { ...cur.dealingRange, lines: { ...cur.dealingRange.lines, ...patch } },
    };
    set({ settings: next });
    persist(next);
  },
  reset: () => {
    const next = clone(DEFAULT_ICT_DISPLAY);
    set({ settings: next });
    persist(next);
  },
}));
