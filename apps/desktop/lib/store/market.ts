"use client";

import { create } from "zustand";
import type { Kline } from "@/lib/types/backend";

// MarketContext — 市场选择状态（symbol/tf/exchange）+ markets WS streamState
// 拆分自原 context.ts 的 pageFilter + symBase 字段
// 消费者：22 个 watch/research 页面 + sidebar + symbol-bar + ctx-bar
// marketsStreamState：use-markets-ws 写入，use-klines 监听 recovering→live 触发 gap 补齐

export interface PageFilter {
  tf: string;
  symbol: string;
  exchange: string;
}

export type MarketsStreamState = "live" | "degraded" | "recovering";

// WS 推送的 KlineEvent（后端 broadcast_market_event type:"kline"）
// kf-page onMessageBatch 写入，use-klines 监听做 SWR 乐观更新
export interface WsKline extends Kline {
  symbol?: string; // 形如 "BTC/USDT"，use-klines 校验匹配
  interval: string;
  closed: boolean;
}

export interface SymbolPick {
  symbol: string;
  market: "crypto" | "us" | "commodities" | "ashare" | "taiwan";
}

interface MarketState {
  symBase: string;
  pageFilter: PageFilter;
  marketsStreamState: MarketsStreamState;
  // 是否见过至少一次 live（冷启动噪音门：false 时行情状态位不显示）
  marketsEverLive: boolean;
  // 最新一条 WS kline（带 seq 单调递增，避免旧消息覆盖新消息）
  lastWsKline: { seq: number; kline: WsKline } | null;
  // ⌘K 选中符号 → 超级图表的显式交接通道（带市场，避开 routeSymbol 歧义）
  symbolPick: SymbolPick | null;
  setSymBase: (sym: string) => void;
  setPageFilter: (filter: Partial<PageFilter>) => void;
  setMarketsStreamState: (s: MarketsStreamState) => void;
  setLastWsKline: (seq: number, kline: WsKline) => void;
  setSymbolPick: (pick: SymbolPick | null) => void;
}

export const useMarketStore = create<MarketState>((set) => ({
  symBase: "BTC",
  pageFilter: {
    tf: "1h",
    symbol: "BTC",
    exchange: "all",
  },
  marketsStreamState: "recovering",
  marketsEverLive: false,
  lastWsKline: null,
  symbolPick: null,
  setSymBase: (sym) =>
    set((s) => ({
      symBase: sym,
      pageFilter: { ...s.pageFilter, symbol: sym },
    })),
  setPageFilter: (filter) =>
    set((s) => ({ pageFilter: { ...s.pageFilter, ...filter } })),
  setMarketsStreamState: (s) =>
    set((st) => ({
      marketsStreamState: s,
      marketsEverLive: st.marketsEverLive || s === "live",
    })),
  setLastWsKline: (seq, kline) =>
    set((s) => {
      // 只接受比当前 seq 更新的消息，防止乱序覆盖
      if (s.lastWsKline && seq <= s.lastWsKline.seq) return s;
      return { lastWsKline: { seq, kline } };
    }),
  setSymbolPick: (pick) => set({ symbolPick: pick }),
}));
